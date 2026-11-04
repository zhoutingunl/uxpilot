"""Idempotently import existing jfcs62 evidence into the recovered workbench."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

from .replay import read_events, read_json, render_replay
from .report import render
from .store import Store


def timestamp(value, fallback=0):
    try:
        return datetime.fromisoformat(value).timestamp()
    except (ValueError, TypeError):
        return fallback


def copy_evidence(source, target):
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if any(part.startswith(".") or part in {"checkout", "__pycache__"} for part in relative.parts):
            continue
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source.resolve()):
            continue
        destination = target / relative
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def replay_events(root, run):
    """Older runs omitted structured stage errors; derive them from saved results."""
    events = read_events(root / "events.jsonl")
    if not any(e.get("kind") == "observation" for e in events):
        for path in sorted((root / "observations").glob("*.json")):
            observation = read_json(path, {})
            if observation:
                events.append(
                    {
                        "kind": "observation",
                        "t_ms": observation.get("t_ms", 0),
                        "step": observation.get("step"),
                        "url": observation.get("url"),
                        "screenshot": observation.get("screenshot"),
                        "derived_from": str(path.relative_to(root)),
                    }
                )
    for field, stage in [("error", "browser"), ("judge_error", "judge")]:
        if run.get(field) and not any(
            e.get("kind") == "stage_error" and e.get("stage") == stage for e in events
        ):
            events.append(
                {
                    "kind": "stage_error",
                    "stage": stage,
                    "error": run[field],
                    "t_ms": run.get("browser_duration_ms" if stage == "browser" else "duration_ms", 0),
                    "derived_from": "run.json#" + field,
                }
            )
    # Recover the action name from the recorded start event for old action-result errors.
    action = None
    for event in events:
        if event.get("kind") == "agent_action_start":
            action = event.get("action", {}).get("kind")
        if event.get("kind") == "agent_action_result" and action:
            event.setdefault("action_kind", action)
    events.sort(key=lambda e: e.get("t_ms", 0))
    return events


def import_history(base: Path):
    store = Store(base / "workbench")
    old_jobs = {}
    for path in (base / "state/jobs").glob("*.json"):
        job = read_json(path, {})
        if job.get("report"):
            old_jobs[Path(job["report"]).parent.name] = job
    imported = []
    for report_file in sorted((base / "artifacts/agent").glob("*/report.json")):
        report = read_json(report_file, {})
        if not report.get("batch_id"):
            continue
        batch = report_file.parent
        old_job = old_jobs.get(batch.name)
        job_id = old_job["id"] if old_job else "legacy-" + batch.name
        already_imported = store.job(job_id) is not None
        if old_job and old_job["status"] in {"queued", "planning", "evaluating"}:
            raise RuntimeError("Finish active legacy tasks before migration")
        backup = batch / ".before-workbench-restore"
        backup.mkdir(exist_ok=True)
        for name in ["report.json", "report.html", "report.md"]:
            source = batch / name
            if source.exists() and not (backup / name).exists():
                shutil.copy2(source, backup / name)
        request = (old_job or {}).get("request", {})
        if old_job:
            for discovery in sorted((base / "artifacts/projects" / job_id).glob("*/discovery"))[:1]:
                copy_evidence(discovery, batch / "planning")
                render_replay(
                    batch / "planning",
                    {
                        "task_id": "任务规划",
                        "goal": request.get("goals", ""),
                        "stop_reason": "已保存规划证据",
                        "judge_status": "not_applicable",
                    },
                )
                report["planning_replay"] = "planning/replay.html"
        runs = report.get("runs", [])
        starts = [timestamp(r.get("started_at")) for r in runs if r.get("started_at")]
        started = timestamp(
            (old_job or {}).get("started_at"), min(starts, default=report_file.stat().st_mtime)
        )
        created = timestamp((old_job or {}).get("created_at"), started)
        finished = timestamp(
            (old_job or {}).get("finished_at"),
            max(
                (timestamp(r.get("started_at"), started) + r.get("duration_ms", 0) / 1000 for r in runs),
                default=started,
            ),
        )
        status = (old_job or {}).get("status")
        if not status:
            status = (
                "interrupted"
                if any(r.get("stop_reason") == "interrupted" for r in runs)
                else (
                    "failed"
                    if any(
                        r.get("judge_status") != "completed" or r.get("stop_reason") == "runtime_error"
                        for r in runs
                    )
                    else "completed"
                )
            )
        payload = {
            "repo": request.get("repo", "https://github.com/zhoutingunl/DuckKing.git"),
            "url": request.get("url", report.get("target_url", "")),
            "goal": request.get("goals", ""),
            "tasks": request.get("tasks", len(runs) or 1),
            "repeats": 1,
            "max_steps": 12,
            "timeout": 300,
            "ref": request.get("ref", ""),
        }
        payload["_release_version"] = (old_job or {}).get("version")
        payload["_imported_from"] = "legacy_web_job" if old_job else "legacy_cli_batch"
        for run in runs:
            folder = batch / run["run_id"]
            if not folder.is_dir() or folder.is_symlink():
                continue
            events = replay_events(folder, run)
            # Derived trace is separate; original events and raw evidence remain unchanged.
            (folder / "imported-events.jsonl").write_text(
                "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)
            )
        render(batch, report)
        root = store.directory / "jobs" / job_id
        destination = root / "history" / batch.name
        copy_evidence(batch, destination)
        copied = read_json(destination / "report.json", {})
        copied["analysis_replay_url"] = f"/api/jobs/{job_id}/analysis"
        render(destination, copied)
        relative = str((destination / "report.html").relative_to(root))
        with store.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO jobs(id,status,request,created_at,started_at,finished_at,stage,report_path) VALUES(?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    status,
                    json.dumps(payload, ensure_ascii=False),
                    created,
                    started,
                    finished,
                    status,
                    relative,
                ),
            )
        with store.connect() as db:
            for run in runs:
                folder = destination / run["run_id"]
                stream = str(folder.relative_to(root))
                for seq, event in enumerate(read_events(folder / "imported-events.jsonl")):
                    store.append_event(
                        job_id,
                        stream,
                        seq,
                        event,
                        timestamp(run.get("started_at"), started) + event.get("t_ms", 0) / 1000,
                        connection=db,
                    )
            planning = destination / "planning"
            if planning.is_dir():
                manifest = read_json(planning / "manifest.json", {})
                for seq, event in enumerate(read_events(planning / "events.jsonl")):
                    store.append_event(
                        job_id,
                        str(planning.relative_to(root)),
                        seq,
                        event,
                        timestamp(manifest.get("started_at"), started) + event.get("t_ms", 0) / 1000,
                        connection=db,
                    )
        if not already_imported:
            imported.append(job_id)
    return {"imported_jobs": imported, "total_jobs": len(store.jobs()), "database": str(store.path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    print(json.dumps(import_history(parser.parse_args().base.resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
