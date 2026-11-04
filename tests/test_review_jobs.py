import asyncio
import fcntl
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ai_ux.review_jobs import JobStore, QueueFull, ReviewRequest
from ai_ux.review_worker import execute_job, run_one


def request(**values):
    return ReviewRequest(
        repo="https://github.com/example/project.git",
        url="https://example.com/demo",
        request_id=values.pop("request_id", "a" * 32),
        **values,
    )


def test_queue_is_persistent_idempotent_and_bounded(tmp_path):
    store = JobStore(tmp_path / "jobs")
    job, created = store.submit(request())
    assert created and job["status"] == "queued"
    assert store.submit(request()) == (job, False)
    assert JobStore(store.directory).read(job["id"]) == job
    with pytest.raises(ValueError):
        store.submit(request(goals="different"))
    for digit in "bcde":
        store.submit(request(request_id=digit * 32))
    with pytest.raises(QueueFull):
        store.submit(request(request_id="f" * 32))
    store.update(job["id"], status="completed")
    assert store.submit(request(request_id="f" * 32))[1]
    assert "request_id" not in JobStore.public(job)


@pytest.mark.parametrize(
    "field,value",
    [
        ("repo", "file:///root/.secrets"),
        ("repo", "git@github.com:owner/repo"),
        ("repo", "https://user:password@github.com/repo"),
        ("repo", "https://github.com/repo?token=secret"),
        ("url", "http://169.254.169.254/latest/meta-data/"),
        ("url", "file:///etc/passwd"),
        ("url", "http://localhost/"),
        ("url", "http://[::1]/"),
        ("url", "https://example.com/?api_key=secret"),
        ("ref", "--upload-pack=evil"),
        ("tasks", 20),
        ("tasks", True),
    ],
)
def test_web_inputs_cannot_select_local_files_or_embed_credentials(field, value):
    data = request().model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        ReviewRequest.model_validate(data)


def test_existing_batch_lock_keeps_new_jobs_queued(tmp_path):
    store = JobStore(tmp_path / "state/jobs")
    job, _ = store.submit(request())
    with (tmp_path / "eval.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run_one(store, tmp_path, "v1") is False
    assert store.read(job["id"])["status"] == "queued"


async def test_worker_connects_discovery_to_reports_and_persists_progress(tmp_path, monkeypatch):
    from ai_ux import review_worker

    store = JobStore(tmp_path / "jobs")
    job, _ = store.submit(request(tasks=1, goals="Search for a result"))
    config = SimpleNamespace(
        app=SimpleNamespace(name="Test product"), tasks=[SimpleNamespace(id="search")], output_dir=None
    )
    repo = object()

    async def discover(repo_value, url, **kwargs):
        assert repo_value == job["request"]["repo"] and kwargs["goals"] == ["Search for a result"]
        assert store.read(job["id"])["status"] == "planning"
        return tmp_path, config, repo

    async def evaluate(value, repository, on_progress):
        assert value.output_dir == str(tmp_path / "reports") and repository is repo
        on_progress({"batch_id": "test-batch", "completed": 0, "total": 1})
        assert store.read(job["id"])["report"] == "reports/test-batch/report.html"
        on_progress({"batch_id": "test-batch", "completed": 1, "total": 1, "last_task": "search"})
        return tmp_path / "reports/test-batch", {
            "runs": [{"stop_reason": "explorer_done", "judge_status": "completed"}]
        }

    monkeypatch.setattr(review_worker, "discover", discover)
    monkeypatch.setattr(review_worker, "evaluate", evaluate)
    await execute_job(store, job, tmp_path / "projects", tmp_path / "reports", "v1")
    result = store.read(job["id"])
    assert result["status"] == "completed" and result["completed_tasks"] == 1 and result["finished_at"]


async def test_worker_failure_and_interruption_do_not_leave_running_jobs(tmp_path, monkeypatch):
    from ai_ux import review_worker

    store = JobStore(tmp_path / "jobs")
    job, _ = store.submit(request())

    async def fail(*args, **kwargs):
        raise ValueError("private-key-must-not-appear")

    monkeypatch.setattr(review_worker, "discover", fail)
    await execute_job(store, job, tmp_path / "projects", tmp_path / "reports", "v1")
    assert store.read(job["id"])["status"] == "failed"
    assert "private-key" not in json.dumps(store.read(job["id"]))

    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(review_worker, "discover", cancel)
    with pytest.raises(asyncio.CancelledError):
        await execute_job(store, job, tmp_path / "projects", tmp_path / "reports", "v1")
    assert store.read(job["id"])["status"] == "interrupted"
