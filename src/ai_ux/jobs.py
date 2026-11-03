"""Bounded persistent workers running the same discovery/evaluation pipeline as the CLI."""

from __future__ import annotations

import asyncio
import fcntl
import ipaddress
import socket
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field

from .defaults import default_models
from .evidence import EVENT_SINK
from .policy import scrub
from .repository import validate_git_url
from .schema import Browser, StrictModel
from .store import Store


class JobRequest(StrictModel):
    repo: str = Field(min_length=1, max_length=1000)
    url: str = Field(min_length=1, max_length=2000)
    goal: str = Field(default="", max_length=1500)
    tasks: int = Field(default=2, ge=1, le=5)
    repeats: int = Field(default=1, ge=1, le=3)
    max_steps: int = Field(default=12, ge=1, le=30)
    timeout: int = Field(default=300, ge=30, le=600)


def public_host(host):
    if not host:
        raise ValueError("地址缺少域名")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [
                ipaddress.ip_address(x[4][0]) for x in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            ]
        except OSError:
            raise ValueError("域名无法解析") from None
    if not addresses or any(not a.is_global for a in addresses):
        raise ValueError("网站模式仅允许公网目标地址")


def validate_request(request: JobRequest, allow_local=False, local_roots=()):
    target = urlsplit(request.url)
    if target.scheme not in {"http", "https"} or not target.hostname or target.username or target.password:
        raise ValueError("演示地址须为不含凭据的 HTTP(S) URL")
    if not allow_local:
        public_host(target.hostname)
    local = Path(request.repo).expanduser()
    if local.is_dir():
        if not any(local.resolve().is_relative_to(p.resolve()) for p in local_roots):
            raise ValueError("本地仓库不在管理员允许的目录内")
    else:
        validate_git_url(request.repo)
        repo = urlsplit(request.repo)
        if repo.scheme != "https":
            raise ValueError("网站提交仅支持 HTTPS Git 仓库地址")
        if not allow_local:
            public_host(repo.hostname)
    return request


class JobManager:
    def __init__(self, store: Store, workers=1, runner=None, allow_local=False):
        self.store = store
        self.workers = workers
        self.runner = runner or self.execute
        self.allow_local = allow_local
        self.tasks = []
        self.active = {}
        self.stopping = False
        self.lock = None

    async def start(self):
        self.lock = (self.store.directory / "worker.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise RuntimeError("该数据目录已有工作进程；网站只允许一个 Uvicorn 进程") from None
        self.store.recover()
        self.tasks = [asyncio.create_task(self.worker()) for _ in range(self.workers)]

    async def stop(self):
        self.stopping = True
        for task in self.active.values():
            task.cancel()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, *self.active.values(), return_exceptions=True)
        if self.lock:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            self.lock.close()

    async def worker(self):
        while not self.stopping:
            job = self.store.claim()
            if not job:
                await asyncio.sleep(0.3)
                continue
            task = asyncio.create_task(self.run(job))
            self.active[job["id"]] = task
            try:
                await task
            except asyncio.CancelledError:
                if self.stopping:
                    raise
            finally:
                self.active.pop(job["id"], None)

    def cancel(self, job_id):
        job = self.store.cancel(job_id)
        if job_id in self.active:
            self.active[job_id].cancel()
        return job

    async def run(self, job):
        job_id = job["id"]
        root = self.store.directory / "jobs" / job_id
        root.mkdir(parents=True, exist_ok=True)

        def sink(path, started, event):
            relative = str(path.resolve().relative_to(root.resolve()))
            at = datetime.fromisoformat(started).timestamp() + event.get("t_ms", 0) / 1000
            self.store.append_event(job_id, relative, event.get("sequence", 0), event, at)
            if event["kind"] == "stage":
                self.store.stage(job_id, event.get("stage", "running"))

        token = EVENT_SINK.set(sink)
        try:
            result = await self.runner(job, root)
            cancelled = self.store.job(job_id)["cancel_requested"]
            status = "cancelled" if cancelled else result.get("status", "completed")
            self.store.finish(job_id, status, result.get("error"), result.get("report_path"))
        except asyncio.CancelledError:
            self.store.finish(
                job_id, "interrupted" if self.stopping else "cancelled", "运行已中断，证据已保留"
            )
        except Exception as e:
            message = str(scrub(str(e)))[:1000]
            self.store.append_event(
                job_id, "job", 1, {"kind": "stage_error", "stage": "job", "error": message}
            )
            self.store.finish(job_id, "failed", message)
        finally:
            EVENT_SINK.reset(token)

    async def execute(self, job, root):
        from .agent import evaluate
        from .discovery import discover

        request = JobRequest.model_validate(job["request"])
        self.store.stage(job["id"], "planning")
        project, config, repo = await discover(
            request.repo,
            request.url,
            output=root,
            goals=[request.goal] if request.goal else [],
            task_count=request.tasks,
            task_steps=request.max_steps,
            task_timeout=request.timeout,
            models=default_models(),
            browser=Browser(video_max_seconds=600),
            allowed_hosts=[urlsplit(request.url).hostname],
            resource_hosts=[],
        )
        self.store.stage(job["id"], "evaluating")
        batch, report = await evaluate(
            config,
            repeats=request.repeats,
            repository=repo,
            report_base_url=f"/api/jobs/{job['id']}/analysis",
        )
        path = str((batch / "report.html").relative_to(root))
        failed = any(
            r.get("judge_status") != "completed"
            or r.get("stop_reason") in {"runtime_error", "task_timeout", "interrupted"}
            for r in report["runs"]
        )
        return {
            "status": "failed" if failed else "completed",
            "report_path": path,
            "error": "部分任务未完成，详见报告与回放" if failed else None,
        }
