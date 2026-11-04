"""The recovered UX Pilot workbench, with stable jfcs62 report and deployment URLs."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from .deployment_web import DeploymentState
from .jobs import JobRequest, validate_request
from .webapp import Settings, create_app


def create_jfcs62_app(base: Path, release: Path, settings=None):
    base = base.resolve()
    identity = json.loads(release.read_text())
    settings = settings or Settings()
    settings.data_dir = base / "workbench"
    settings.version = identity["version"]
    settings.max_queued = 5
    app = create_app(settings)
    deployment = DeploymentState(release, base / "state", base / "artifacts/agent")
    store = app.state.store
    # These endpoints retain the already deployed journal and its snapshot identity.
    app.router.routes = [
        r for r in app.router.routes if getattr(r, "path", None) not in {"/api/health", "/deploys"}
    ]

    @app.middleware("http")
    async def request_boundary(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "请从本网站提交操作"}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "请求需要 JSON 数据"}, status_code=415)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "release": deployment.current,
            "baseline": identity.get("baseline"),
            "features": ["jobs", "analysis_replay", "error_clusters", "statistics", "deployment_journal"],
        }

    @app.get("/deploys")
    async def deploys_alias():
        return RedirectResponse("/deploys.html")

    @app.get("/deploys.html", response_class=HTMLResponse)
    async def deploys():
        return HTMLResponse((Path(__file__).parent / "web/deploys.html").read_text())

    @app.get("/deploys.js")
    async def deploys_js():
        return FileResponse(Path(__file__).parent / "web/deploys.js", media_type="text/javascript")

    @app.get("/deploys.css")
    async def deploys_css():
        return FileResponse(Path(__file__).parent / "web/deploys.css", media_type="text/css")

    @app.get("/api/deployments")
    async def deployment_info():
        value = deployment.snapshot()
        latest = store.jobs(1)
        if latest:
            row = latest[0]

            def stamp(t):
                return datetime.fromtimestamp(t, timezone.utc).isoformat() if t else None

            value["evaluation"] = {
                "status": row["status"],
                "started_at": stamp(row["started_at"]),
                "finished_at": stamp(row["finished_at"]),
                "version": row["request"].get("_release_version"),
            }
        return value

    @app.get("/reports/{relative:path}")
    async def legacy_report(relative: str):
        target = deployment.report_file("/reports/" + relative)
        if not target:
            raise HTTPException(404, "文件不存在")
        return FileResponse(target)

    @app.get("/api/reviews")
    async def legacy_jobs():
        def translate(job):
            request = job["request"]
            return {
                "id": job["id"],
                "status": job["status"],
                "request": {**request, "goals": request.get("goal", "")},
                "report": f"/api/jobs/{job['id']}/reports/{job['report_path']}"
                if job["report_path"]
                else None,
                "analysis": f"/api/jobs/{job['id']}/analysis",
                "error": job["error"],
            }

        return {"jobs": [translate(job) for job in store.jobs()]}

    @app.post("/api/reviews")
    async def legacy_submit(request: Request):
        body = await request.json()
        data = {
            "repo": body.get("repo"),
            "url": body.get("url"),
            "goal": body.get("goals", ""),
            "tasks": body.get("tasks", 3),
            "ref": body.get("ref", ""),
        }
        try:
            item = JobRequest.model_validate(data)
            validate_request(item, allow_local=settings.allow_local, local_roots=settings.allowed_local_roots)
            job = store.create_job(item.model_dump(), max_queued=settings.max_queued)
        except ValueError:
            raise HTTPException(422, "请检查仓库与网站地址") from None
        return JSONResponse({"job": job, "created": True}, status_code=202)

    return app


def main():
    import uvicorn

    base = Path(os.environ.get("UXPILOT_DEPLOY_BASE", "/root/zt_dir/ai_base/DuckKing_ux_review"))
    app = create_jfcs62_app(base, Path("release.json").resolve())
    uvicorn.run(
        app,
        host=os.environ.get("UXPILOT_BIND", "10.210.32.30"),
        port=int(os.environ.get("UXPILOT_PORT", "18765")),
        workers=1,
    )


if __name__ == "__main__":
    main()
