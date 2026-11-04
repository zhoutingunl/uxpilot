"""UX Pilot Workbench: asynchronous jobs, analysis replay, error clusters, stats and deploy journal."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .analytics import statistics
from .deployments import DeploymentRecord, process_identity, snapshot
from .jobs import JobManager, JobRequest, validate_request
from .replay import render_replay
from .store import Store


class Settings:
    def __init__(self):
        self.data_dir = Path(os.environ.get("UXPILOT_DATA_DIR", "artifacts/workbench")).expanduser().resolve()
        self.allowed_local_roots = [
            Path(p).expanduser().resolve()
            for p in os.environ.get("UXPILOT_LOCAL_ROOTS", "").split(os.pathsep)
            if p
        ]
        self.allow_local = os.environ.get("UXPILOT_ALLOW_LOCAL", "0") == "1"
        self.max_queued = int(os.environ.get("UXPILOT_MAX_QUEUED", "100"))
        self.workers = int(os.environ.get("UXPILOT_WORKERS", "1"))
        self.version = os.environ.get("UXPILOT_VERSION", "development")
        self.commit = os.environ.get("UXPILOT_COMMIT", "unknown")
        self.release_file = (
            Path(os.environ.get("UXPILOT_RELEASE_FILE", ""))
            if os.environ.get("UXPILOT_RELEASE_FILE")
            else None
        )


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    store = Store(settings.data_dir)
    manager = JobManager(store, workers=settings.workers, allow_local=settings.allow_local)

    @asynccontextmanager
    async def lifespan(app):
        await manager.start()
        try:
            yield
        finally:
            await manager.stop()

    app = FastAPI(title="UX Pilot Workbench", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.manager = manager
    static = Path(__file__).parent / "webstatic"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def home():
        return HTMLResponse((static / "index.html").read_text())

    @app.get("/jobs", response_class=HTMLResponse)
    async def jobs_page():
        return HTMLResponse((static / "jobs.html").read_text())

    @app.get("/stats", response_class=HTMLResponse)
    async def stats_page():
        return HTMLResponse((static / "stats.html").read_text())

    @app.get("/errors", response_class=HTMLResponse)
    async def errors_page():
        return HTMLResponse((static / "errors.html").read_text())

    @app.get("/deploys", response_class=HTMLResponse)
    async def deploys_page():
        return HTMLResponse((static / "deploys.html").read_text())

    @app.post("/api/admin/reset")
    async def admin_reset(request: Request):
        if os.environ.get("UXPILOT_ENABLE_ADMIN_RESET", "0") != "1":
            raise HTTPException(404, "not found")
        if request.headers.get("x-uxpilot-admin") != os.environ.get("UXPILOT_ADMIN_TOKEN", ""):
            raise HTTPException(403, "forbidden")
        store.delete_all()
        return {"ok": True}

    @app.get("/api/health")
    async def health():
        return {
            "ok": True,
            "version": settings.version,
            "commit": settings.commit,
            "queued": sum(j["status"] == "queued" for j in store.jobs()),
        }

    @app.post("/api/jobs")
    async def create_job(request: Request):
        try:
            body = JobRequest.model_validate(await request.json())
            validate_request(body, allow_local=settings.allow_local, local_roots=settings.allowed_local_roots)
            return JSONResponse(
                store.create_job(body.model_dump(), max_queued=settings.max_queued) or {"status": "queued"},
                status_code=202,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/jobs")
    async def list_jobs(limit: int = Query(100, ge=1, le=200)):
        return {"jobs": store.jobs(limit)}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        job = store.job(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        return job

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        job = manager.cancel(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        return job

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, after: int = Query(0, ge=0)):
        if not store.job(job_id):
            raise HTTPException(404, "任务不存在")
        return {"events": store.events(job_id, after)}

    def job_root(job_id: str):
        return (settings.data_dir / "jobs" / job_id).resolve()

    def safe_job_file(job_id: str, relative: str):
        if not store.job(job_id) or Path(job_id).name != job_id:
            raise HTTPException(404, "任务不存在")
        root = job_root(job_id)
        parts = Path(relative).parts
        if Path(relative).is_absolute() or any(p.startswith(".") or p == "checkout" for p in parts):
            raise HTTPException(404, "文件不存在")
        raw = root / relative
        target = raw.resolve()
        if any(p.is_symlink() for p in [raw, *raw.parents] if p != root):
            raise HTTPException(404, "文件不存在")
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(404, "文件不存在")
        return target

    @app.get("/api/jobs/{job_id}/reports/{relative_path:path}")
    async def job_report(job_id: str, relative_path: str):
        target = safe_job_file(job_id, relative_path)
        media = "text/html; charset=utf-8" if target.suffix == ".html" else None
        return FileResponse(target, media_type=media)

    @app.get("/api/jobs/{job_id}/analysis")
    async def job_analysis(job_id: str):
        job = store.job(job_id)
        if not job or not job.get("report_path"):
            raise HTTPException(409, "任务尚未生成报告")
        report = safe_job_file(job_id, job["report_path"])
        target = report.parent / "analysis-replay.html"
        if not target.is_file():
            raise HTTPException(409, "分析回放尚未生成")
        relative = target.relative_to(job_root(job_id))
        return RedirectResponse(f"/api/jobs/{job_id}/reports/{relative.as_posix()}")

    @app.get("/api/jobs/{job_id}/replay")
    async def job_replay(job_id: str):
        job = store.job(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        run_files = list(job_root(job_id).rglob("run.json"))
        if not run_files:
            raise HTTPException(409, "任务尚未生成运行证据")
        for run_file in run_files:
            root = run_file.parent
            if not (root / "replay.html").is_file():
                render_replay(root, json.loads(run_file.read_text()))
        relative = run_files[0].parent.relative_to(job_root(job_id)) / "replay.html"
        return RedirectResponse(f"/api/jobs/{job_id}/reports/{relative.as_posix()}")

    @app.get("/api/stats")
    async def stats(days: int = Query(30, ge=1, le=365)):
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        jobs, calls = store.analytics_rows(start.timestamp(), end.timestamp())
        return statistics(jobs, calls, start.date(), end.date())

    @app.get("/api/errors")
    async def errors(days: int = Query(30, ge=1, le=365), state: str | None = None):
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        clusters = store.clusters(start.timestamp(), end.timestamp(), state)
        for cluster in clusters:
            for sample in cluster["samples"]:
                sample["job_url"] = f"/jobs#{sample['job_id']}"
                relative = Path(sample.get("stream") or "") / "replay.html"
                try:
                    target = safe_job_file(sample["job_id"], relative.as_posix())
                    if target.is_file():
                        sample["replay_url"] = (
                            f"/api/jobs/{sample['job_id']}/reports/{relative.as_posix()}#event-{sample['seq']}"
                        )
                except HTTPException:
                    pass
        return {"clusters": clusters}

    @app.post("/api/errors/triage")
    async def triage(request: Request):
        body = await request.json()
        try:
            store.triage(body.get("cluster_ids"), body.get("state"), body.get("note", ""))
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from None
        return {"ok": True}

    @app.get("/api/deploys")
    async def deploys():
        return snapshot(process_identity(settings.release_file), store.deployments())

    @app.post("/api/deploys")
    async def record_deploy(request: Request):
        # Recording is explicit; the web worker never labels its own running process as deployed.
        try:
            record = DeploymentRecord.model_validate(await request.json())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        store.record_deployment({**record.model_dump(), "at": datetime.now(timezone.utc).timestamp()})
        return {"ok": True, "record": record.model_dump()}

    return app


app = create_app()


def web_main():
    import uvicorn

    uvicorn.run(
        "ai_ux.webapp:app", host="127.0.0.1", port=int(os.environ.get("UXPILOT_PORT", "8088")), workers=1
    )
