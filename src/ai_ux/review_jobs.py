"""Persistent, bounded web-review queue shared by the UI and its separate worker."""

from __future__ import annotations

import fcntl
import ipaddress
import json
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from pydantic import Field, field_validator

from .discovery import check_demo_url
from .repository import validate_git_url
from .schema import StrictModel

ACTIVE = {"queued", "planning", "evaluating"}
JOB_ID = re.compile(r"^\d{8}T\d{6}Z-[a-f0-9]{12}$")


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_host(value):
    host = urlsplit(value).hostname
    if not host or host.lower() == "localhost" or host.lower().endswith(".localhost"):
        raise ValueError("Loopback targets are not available through the web form")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return value
    if address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:
        raise ValueError("Invalid evaluation target")
    return value


class ReviewRequest(StrictModel):
    repo: str = Field(min_length=8, max_length=500)
    url: str = Field(min_length=8, max_length=2000)
    goals: str = Field(default="", max_length=1500)
    tasks: int = Field(default=3, ge=1, le=5, strict=True)
    ref: str = Field(default="", max_length=150)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")

    @field_validator("repo")
    @classmethod
    def repo_url(cls, value):
        value = value.strip()
        if urlsplit(value).scheme != "https":
            raise ValueError("Use an HTTPS Git repository address")
        return validate_host(validate_git_url(value))

    @field_validator("url")
    @classmethod
    def demo_url(cls, value):
        value = validate_host(check_demo_url(value.strip()))
        if any(
            re.search(r"password|secret|token|api.?key", key, re.I)
            for key, _ in parse_qsl(urlsplit(value).query)
        ):
            raise ValueError("Do not include credentials in demo addresses")
        return value

    @field_validator("ref")
    @classmethod
    def repository_ref(cls, value):
        value = value.strip()
        if value and (value.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_./-]+", value)):
            raise ValueError("Invalid branch or tag")
        return value


class QueueFull(ValueError):
    pass


class JobStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    @contextmanager
    def locked(self):
        with self.lock, (self.directory / ".queue.lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def path(self, job_id):
        if not JOB_ID.fullmatch(job_id):
            raise ValueError("Invalid job id")
        return self.directory / (job_id + ".json")

    def read(self, job_id):
        return json.loads(self.path(job_id).read_text())

    def save(self, job):
        destination = self.path(job["id"])
        temp = destination.with_suffix("." + uuid.uuid4().hex + ".tmp")
        temp.write_text(json.dumps(job, ensure_ascii=False, indent=2))
        temp.chmod(0o600)
        temp.replace(destination)

    def list(self):
        jobs = []
        for path in sorted(self.directory.glob("*.json"), reverse=True)[:200]:
            if not JOB_ID.fullmatch(path.stem) or path.is_symlink():
                continue
            try:
                jobs.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                continue
        return jobs

    def submit(self, request: ReviewRequest):
        with self.locked():
            existing = self.list()
            payload = request.model_dump(exclude={"request_id"})
            for job in existing:
                if job.get("request_id") == request.request_id:
                    if job["request"] != payload:
                        raise ValueError("Request id already used for different input")
                    return job, False
            if sum(job.get("status") in ACTIVE for job in existing) >= 5:
                raise QueueFull("Queue full")
            job = {
                "id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12],
                "request_id": request.request_id,
                "request": payload,
                "status": "queued",
                "created_at": now(),
                "completed_tasks": 0,
                "total_tasks": request.tasks,
                "report": None,
            }
            self.save(job)
            return job, True

    def update(self, job_id, **values):
        with self.locked():
            job = self.read(job_id)
            job.update(values)
            self.save(job)
            return job

    @staticmethod
    def public(job):
        return {
            key: job.get(key)
            for key in (
                "id",
                "request",
                "status",
                "created_at",
                "started_at",
                "finished_at",
                "completed_tasks",
                "total_tasks",
                "report",
                "error",
                "product_name",
                "last_task",
                "exit_code",
                "version",
            )
        }
