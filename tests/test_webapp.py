import asyncio
import time

from fastapi.testclient import TestClient

from ai_ux.webapp import Settings, create_app


def client(tmp_path, monkeypatch):
    monkeypatch.setenv("UXPILOT_DATA_DIR", str(tmp_path / "db"))
    monkeypatch.setenv("UXPILOT_ALLOW_LOCAL", "0")
    app = create_app(Settings())

    async def waiting_runner(job, root):
        await asyncio.sleep(3600)

    app.state.manager.runner = waiting_runner
    return TestClient(app)


def test_workbench_pages_and_async_job_lifecycle(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as c:
        assert c.get("/").status_code == 200
        for path in ["/jobs", "/stats", "/errors", "/deploys"]:
            assert c.get(path).status_code == 200
        bad = c.post(
            "/api/jobs", json={"repo": "https://github.com/org/repo.git", "url": "http://127.0.0.1:8000"}
        )
        assert bad.status_code == 422
        created = c.post(
            "/api/jobs",
            json={"repo": "https://github.com/org/repo.git", "url": "https://github.com/", "tasks": 1},
        ).json()
        assert created["status"] == "queued"
        job = c.get("/api/jobs/" + created["id"]).json()
        assert job["request"]["url"] == "https://github.com/"
        # Exercise cancellation after the worker has claimed the job, without network I/O.
        for _ in range(100):
            if c.get("/api/jobs/" + created["id"]).json()["status"] == "running":
                break
            time.sleep(0.01)
        assert c.get("/api/jobs/" + created["id"]).json()["status"] == "running"
        cancelled = c.post("/api/jobs/" + created["id"] + "/cancel").json()
        assert cancelled["cancel_requested"] == 1
        for _ in range(100):
            if c.get("/api/jobs/" + created["id"]).json()["status"] == "cancelled":
                break
            time.sleep(0.01)
        assert c.get("/api/jobs/" + created["id"]).json()["status"] == "cancelled"
        assert c.get("/api/jobs/" + created["id"] + "/events").status_code == 200


def test_error_stats_deploy_api_and_secure_file_scope(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as c:
        # Store a real normalized error and call through public endpoint.
        store = c.app.state.store
        job = store.create_job({"repo": "https://github.com/x/y.git", "url": "https://x.example"})
        store.append_event(
            job["id"], "events", 0, {"kind": "stage_error", "stage": "judge", "error": "HTTP 529 overload"}
        )
        store.append_event(
            job["id"], "events", 1, {"kind": "stage_error", "stage": "judge", "error": "HTTP 529 overload"}
        )
        errors = c.get("/api/errors?days=90").json()["clusters"]
        assert errors[0]["count"] == 2
        key = errors[0]["cluster_id"]
        assert c.post("/api/errors/triage", json={"cluster_ids": [key], "state": "fixed"}).status_code == 200
        assert c.get("/api/errors?state=fixed").json()["clusters"][0]["state"] == "fixed"
        stats = c.get("/api/stats?days=30").json()
        assert "models" in stats and "daily" in stats
        assert c.get("/api/deploys").json()["state"] == "development"
        assert (
            c.post(
                "/api/deploys",
                json={
                    "version": "v1",
                    "commit": "a" * 40,
                    "built_at": "2026-11-03T00:00:00Z",
                    "target": "demo",
                    "status": "success",
                    "checks": {"health": True, "pages": True, "assets": True, "version": True},
                },
            ).status_code
            == 200
        )
        assert c.get("/api/deploys").json()["records"][0]["version"] == "v1"
        assert c.get("/api/jobs/does-not-exist/reports/../../etc/passwd").status_code in {404, 400}


def test_store_lock_recovery_is_explicit(tmp_path):
    from ai_ux.store import Store

    s = Store(tmp_path / "db")
    job = s.create_job({"repo": "https://github.com/x/y.git", "url": "https://x.example"})
    assert s.claim()
    assert s.recover() == 1 and s.job(job["id"])["status"] == "interrupted"
