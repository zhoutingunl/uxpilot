import json

import pytest

from ai_ux.release_checks import ensure_idle
from ai_ux.store import Store


def test_release_blocks_queued_and_running_work_without_changing_jobs(tmp_path):
    store = Store(tmp_path / "workbench")
    job = store.create_job({"repo": "https://example.com/repo.git", "url": "https://example.com"})
    with pytest.raises(RuntimeError, match="Workbench"):
        ensure_idle(tmp_path)
    assert store.job(job["id"])["status"] == "queued"
    store.claim()
    with pytest.raises(RuntimeError, match="Workbench"):
        ensure_idle(tmp_path)
    assert store.job(job["id"])["status"] == "running"
    store.finish(job["id"], "completed")
    ensure_idle(tmp_path)


def test_release_preserves_legacy_jobs_and_accepts_fresh_install(tmp_path):
    ensure_idle(tmp_path)
    path = tmp_path / "state/jobs/demo.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"status": "evaluating"}))
    with pytest.raises(RuntimeError, match="Legacy"):
        ensure_idle(tmp_path)
    assert json.loads(path.read_text())["status"] == "evaluating"
    path.write_text(json.dumps({"status": "completed"}))
    ensure_idle(tmp_path)
