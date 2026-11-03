import time
from datetime import date

import pytest

from ai_ux.analytics import classify_error, distribution, statistics
from ai_ux.jobs import JobRequest, validate_request
from ai_ux.store import Store


def test_error_clusters_normalize_ids_urls_and_values():
    a = classify_error("browser.click", "HTTP 500 request https://example.com/jobs/abc123 id=123456")
    b = classify_error("browser.click", "HTTP 500 request https://example.com/jobs/def456 id=999999")
    assert a["cluster_id"] == b["cluster_id"] and a["category"] == "upstream"
    assert "<url>" in a["signature"] and "<number>" in a["signature"]


def test_distribution_buckets_and_empty():
    d = distribution([])
    assert d["count"] == 0 and d["p95"] is None
    d = distribution([1, 2, 3, 4, 5])
    assert d["p50"] == 3 and sum(n for _, n in d["buckets"]) == 5


def test_store_jobs_events_calls_errors_triage_and_recovery(tmp_path):
    store = Store(tmp_path / "db")
    job = store.create_job(
        {"repo": "https://example.com/repo.git", "url": "https://example.com"}, max_queued=2
    )
    assert store.claim()["id"] == job["id"]
    store.stage(job["id"], "browser")
    events = [
        {
            "kind": "model_call",
            "provider": "zhipu",
            "model": "glm",
            "status": "ok",
            "duration_ms": 3,
            "usage": {"total_tokens": 10},
        },
        {"kind": "stage_error", "stage": "judge", "error": "HTTP 529 overload"},
        {"kind": "stage_error", "stage": "judge", "error": "HTTP 529 overload"},
    ]
    for i, e in enumerate(events):
        store.append_event(job["id"], "events.jsonl", i, e)
    clusters = store.clusters(0, time.time())
    assert len(clusters) == 1 and clusters[0]["count"] == 2
    assert clusters[0]["candidate"] is False
    store.triage([clusters[0]["cluster_id"]], "investigating", "batch fix")
    assert store.clusters(0, time.time())[0]["state"] == "investigating"
    store.finish(job["id"], "completed", report_path="report.html")
    assert store.job(job["id"])["status"] == "completed"
    assert store.events(job["id"])[0]["payload"]["kind"] == "model_call"
    j2 = store.create_job({"repo": "x", "url": "y"})
    assert store.claim()["id"] == j2["id"]
    assert store.recover() == 1
    assert store.job(j2["id"])["status"] == "interrupted"


def test_store_queue_limit_and_deploy_immutability(tmp_path):
    s = Store(tmp_path / "db")
    s.create_job({"a": 1}, max_queued=1)
    with pytest.raises(ValueError):
        s.create_job({"a": 2}, max_queued=1)
    s.record_deployment(
        {
            "at": 1,
            "version": "v1",
            "commit": "a" * 40,
            "target": "demo",
            "status": "success",
            "checks": {"health": True},
        }
    )
    s.record_deployment(
        {
            "at": 2,
            "version": "v1",
            "commit": "a" * 40,
            "target": "demo",
            "status": "success",
            "checks": {"health": True},
        }
    )
    assert len(s.deployments()) == 1


def test_statistics_separates_queue_execution_total_and_unknown_tokens(tmp_path):
    now = time.time()
    jobs = [
        {"created_at": now - 100, "started_at": now - 80, "finished_at": now - 20, "status": "completed"},
        {"created_at": now - 50, "started_at": None, "finished_at": None, "status": "queued"},
    ]
    calls = [
        {
            "at": now - 70,
            "provider": "z",
            "model": "m",
            "status": "ok",
            "duration_ms": 100,
            "total_tokens": 12,
            "prompt_tokens": 7,
            "completion_tokens": 5,
        },
        {
            "at": now - 40,
            "provider": "z",
            "model": "m",
            "status": "error",
            "duration_ms": 200,
            "total_tokens": None,
        },
    ]
    d = statistics(jobs, calls, date.today(), date.today())
    assert d["submitted"] == 2
    assert (
        d["total_seconds"]["count"] == 1
        and d["execution_seconds"]["count"] == 1
        and d["queue_seconds"]["count"] == 1
    )
    assert d["known_total_tokens"] == 12 and d["unknown_usage_calls"] == 1


def test_job_request_validation_avoids_private_targets():
    body = JobRequest(repo="https://github.com/org/repo.git", url="https://example.com/demo")
    assert validate_request(body, allow_local=False).url.startswith("https://")
    for value in ["http://127.0.0.1:8000", "http://localhost:8000"]:
        with pytest.raises(ValueError):
            validate_request(body.model_copy(update={"url": value}), allow_local=False)
