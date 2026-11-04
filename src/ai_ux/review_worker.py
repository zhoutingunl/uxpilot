"""Execute queued reviews outside the web process; preserve jobs across UI restarts."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from urllib.parse import urlsplit

from .agent import evaluate
from .cli import evaluation_exit
from .discovery import discover
from .review_jobs import JobStore, ReviewRequest, now


async def execute_job(store, job, projects, reports, version):
    request = ReviewRequest(**job["request"], request_id=job["request_id"])
    store.update(job["id"], status="planning", started_at=now(), version=version, error=None)
    try:
        _, config, repo = await discover(
            request.repo,
            request.url,
            output=projects / job["id"],
            ref=request.ref or None,
            goals=[request.goals] if request.goals.strip() else [],
            task_count=request.tasks,
            adapter="duckking" if urlsplit(request.url).hostname == "duck.qbox.net" else "generic",
        )
        config.output_dir = str(reports)
        store.update(
            job["id"], status="evaluating", product_name=config.app.name, total_tasks=len(config.tasks)
        )

        def progress(value):
            store.update(
                job["id"],
                completed_tasks=value["completed"],
                total_tasks=value["total"],
                report=f"reports/{value['batch_id']}/report.html",
                last_task=value.get("last_task"),
            )

        root, result = await evaluate(config, repository=repo, on_progress=progress)
        code = evaluation_exit(result)
        store.update(
            job["id"],
            status="completed" if code == 0 else "failed",
            exit_code=code,
            finished_at=now(),
            report=f"reports/{root.name}/report.html",
            error=None if code == 0 else "部分任务或视频评审未完成，请打开报告查看已保存的证据。",
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        store.update(
            job["id"], status="interrupted", finished_at=now(), error="评测进程已中断，已有报告和证据保留。"
        )
        raise
    except Exception as exc:
        # Never expose request bodies, provider errors, source paths or credentials.
        store.update(
            job["id"],
            status="failed",
            finished_at=now(),
            error=("评测未能完成。请检查仓库是否可读取、网站是否能从服务器访问，再重试。"),
            error_type=type(exc).__name__,
        )
        print(f"Review {job['id']} failed: {type(exc).__name__}", flush=True)


def run_one(store, base, version):
    # Shared with the existing fixed DuckKing batch. Queued jobs wait for it.
    with (base / "eval.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        queued = [j for j in reversed(store.list()) if j["status"] == "queued"]
        if not queued:
            return False
        job = queued[0]
        log = base / "logs" / ("review-" + job["id"] + ".log")
        with log.open("a", buffering=1) as stream, redirect_stdout(stream), redirect_stderr(stream):
            asyncio.run(
                execute_job(store, job, base / "artifacts/projects", base / "artifacts/agent", version)
            )
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    args = parser.parse_args()
    base = args.base.resolve()
    store = JobStore(base / "state/jobs")
    (base / "logs").mkdir(exist_ok=True)
    version = json.loads(Path("release.json").read_text())["version"]
    with (store.directory / ".worker.lock").open("a") as exclusive:
        fcntl.flock(exclusive, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # A service restart must not silently retry a potentially costly review.
        for job in store.list():
            if job["status"] in {"planning", "evaluating"}:
                store.update(
                    job["id"],
                    status="interrupted",
                    finished_at=now(),
                    error="服务重启中断了此任务，已有证据保留。",
                )
        try:
            while True:
                if not run_one(store, base, version):
                    time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
