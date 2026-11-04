import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from ai_ux.jfcs62_web import create_jfcs62_app
from ai_ux.restore_history import import_history
from ai_ux.store import Store
from ai_ux.webapp import Settings


def history(base):
    batch = base / "artifacts/agent/20261104T064007Z-abcdef"
    run = batch / "first-nod-01-abcdef"
    (run / "observations").mkdir(parents=True)
    (run / "screenshots").mkdir()
    data = {
        "run_id": run.name,
        "task_id": "first-nod",
        "attempt": 1,
        "goal": "点头",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": 2000,
        "stop_reason": "runtime_error",
        "judge_status": "not_run",
        "error": "Executable missing ffmpeg",
        "verification": {"task_outcome": "not_verified"},
        "video": {"status": "disabled"},
    }
    (run / "run.json").write_text(json.dumps(data))
    (run / "observations/step-000.json").write_text(
        json.dumps(
            {
                "step": 0,
                "t_ms": 100,
                "screenshot": "screenshots/step-000.png",
                "url": "https://duck.qbox.net/",
            }
        )
    )
    (run / "screenshots/step-000.png").write_bytes(b"fixture")
    events = [
        {
            "kind": "model_call",
            "t_ms": 500,
            "duration_ms": 300,
            "provider": "test",
            "model": "test",
            "status": "error",
            "error": "http_529",
        }
    ]
    original = "".join(json.dumps(e) + "\n" for e in events)
    (run / "events.jsonl").write_text(original)
    report = {
        "batch_id": batch.name,
        "app_name": "DuckKing",
        "target_url": "https://duck.qbox.net/",
        "runs": [data],
        "issues": [],
    }
    (batch / "report.json").write_text(json.dumps(report))
    (batch / "report.html").write_text("old report")
    return batch, run, original


def test_restore_history_is_idempotent_preserves_raw_evidence_and_backfills_real_errors(tmp_path):
    batch, run, original = history(tmp_path)
    result = import_history(tmp_path)
    assert len(result["imported_jobs"]) == 1
    # Simulate interruption after the durable job row but before its audit facts.
    partial = Store(tmp_path / "workbench")
    with partial.connect() as db:
        for table in ("events", "calls", "errors"):
            db.execute("DELETE FROM " + table)
    assert import_history(tmp_path)["imported_jobs"] == []
    assert (run / "events.jsonl").read_text() == original
    assert (batch / ".before-workbench-restore/report.html").read_text() == "old report"
    assert (batch / "analysis-replay.html").exists()
    assert 'id="play"' in (run / "replay.html").read_text()
    store = Store(tmp_path / "workbench")
    job = store.jobs()[0]
    assert job["status"] == "failed"
    assert job["finished_at"] - job["started_at"] == 2
    assert len(store.clusters(0, 9999999999)) == 2
    with store.connect() as db:
        calls = db.execute("SELECT * FROM calls").fetchall()
        assert len(calls) == 1 and calls[0]["total_tokens"] is None
    assert (store.directory / "jobs" / job["id"] / job["report_path"]).exists()


def test_recovered_workbench_keeps_features_and_old_links_without_serving_checkout(tmp_path, monkeypatch):
    batch, run, _ = history(tmp_path)
    import_history(tmp_path)
    release = tmp_path / "release.json"
    release.write_text(json.dumps({"version": "restored", "snapshot_sha256": "a" * 64}))
    monkeypatch.setenv("UXPILOT_DATA_DIR", str(tmp_path / "workbench"))
    with TestClient(create_jfcs62_app(tmp_path, release, Settings())) as client:
        for url in ["/", "/jobs", "/stats", "/errors", "/deploys.html", "/api/stats", "/api/errors"]:
            assert client.get(url).status_code == 200, url
        assert client.get("/deploys", follow_redirects=False).headers["location"] == "/deploys.html"
        job = client.get("/api/jobs").json()["jobs"][0]
        assert client.get("/reports/" + batch.name + "/report.html").status_code == 200
        analysis = client.get("/api/jobs/" + job["id"] + "/analysis")
        assert analysis.status_code == 200 and "分析回放" in analysis.text
        cluster = client.get("/api/errors").json()["clusters"][0]
        assert client.get(cluster["samples"][0]["replay_url"]).status_code == 200
        root = tmp_path / "workbench/jobs" / job["id"]
        (root / "checkout").mkdir()
        (root / "checkout/README.md").write_text("PRIVATE CODE")
        assert client.get("/api/jobs/" + job["id"] + "/reports/checkout/README.md").status_code == 404
        assert (
            client.post(
                "/api/errors/triage",
                json={"cluster_ids": [cluster["cluster_id"]], "state": "fixed"},
                headers={"Origin": "https://unrelated.example"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/errors/triage", json={"cluster_ids": [cluster["cluster_id"]], "state": "investigating"}
            ).status_code
            == 200
        )
        assert client.get("/api/errors?state=investigating").json()["clusters"][0]["state"] == "investigating"
        assert client.get("/api/health").json()["release"]["version"] == "restored"
