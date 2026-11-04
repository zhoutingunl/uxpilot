import json
import threading
from functools import partial
from http.server import ThreadingHTTPServer

import httpx
import pytest

from ai_ux.deployment_web import DeploymentState, Handler


@pytest.fixture
def deployment(tmp_path):
    release = tmp_path / "release.json"
    release.write_text(json.dumps({"version": "v2", "snapshot_sha256": "a" * 64, "private": "not-public"}))
    reports = tmp_path / "reports"
    reports.mkdir()
    return DeploymentState(release, tmp_path, reports)


def test_pending_failure_and_version_mismatch_are_not_success(deployment):
    history = deployment.state / "deployments.jsonl"
    history.write_text(json.dumps({"version": "v1", "snapshot_sha256": "b" * 64, "status": "success"}) + "\n")
    assert deployment.snapshot()["state"] == "mismatch"
    with history.open("a") as stream:
        stream.write(json.dumps({"version": "v2", "snapshot_sha256": "a" * 64, "status": "staged"}) + "\n")
    assert deployment.snapshot()["state"] == "staged"
    with history.open("a") as stream:
        stream.write(json.dumps({"version": "v2", "snapshot_sha256": "a" * 64, "status": "failed"}) + "\n")
    assert deployment.snapshot()["state"] == "mismatch"
    with history.open("a") as stream:
        stream.write(json.dumps({"version": "v2", "snapshot_sha256": "a" * 64, "status": "success"}) + "\n")
    assert deployment.snapshot()["state"] == "ok"
    assert "private" not in deployment.snapshot()["current"]
    (deployment.state / "release.json").write_text('{"version":"v3"}')
    assert deployment.snapshot()["current"]["version"] == "v2"


def test_wrong_coverage_and_corrupt_rows_are_visible(deployment):
    row = {
        "version": "v2",
        "snapshot_sha256": "a" * 64,
        "status": "success",
        "coverage": {"snapshot_sha256": "b" * 64},
    }
    (deployment.state / "deployments.jsonl").write_text(json.dumps(row) + "\nBROKEN\n")
    snapshot = deployment.snapshot()
    assert snapshot["records"][0]["coverage"] is None
    assert snapshot["warnings"]


def test_report_traversal_hidden_paths_and_symlinks_are_rejected(deployment):
    outside = deployment.state / "secret.json"
    outside.write_text('{"private":true}')
    (deployment.reports / "leak.json").symlink_to(outside)
    (deployment.reports / ".env.json").write_text("{}")
    for path in [
        "/reports/../secret.json",
        "/reports/%2e%2e/secret.json",
        "/reports/leak.json",
        "/reports/.env.json",
    ]:
        assert deployment.report_file(path) is None
    report = deployment.reports / "batch" / "report.html"
    report.parent.mkdir()
    report.write_text("<title>Report</title>")
    assert deployment.report_file("/reports/batch/report.html") == report


def test_http_only_exposes_page_api_and_reports(deployment):
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, deployment=deployment))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}") as client:
            assert client.get("/deploys.html").status_code == 200
            assert client.get("/deploys.js").status_code == 200
            assert client.get("/api/health").json()["release"]["version"] == "v2"
            assert client.get("/api/deployments").json()["state"] == "unrecorded"
            assert client.get("/release.json").status_code == 404
            assert client.get("/src/ai_ux/llm.py").status_code == 404
            assert client.post("/api/deployments", json={}).status_code == 404
            assert 'id="review-form"' in client.get("/").text
            assert 'id="review-form"' not in client.get("/deploys.html").text
            payload = {
                "repo": "https://github.com/example/repo.git",
                "url": "https://example.com/",
                "request_id": "a" * 32,
            }
            assert client.post("/api/reviews", json=payload).status_code == 403
            headers = {"X-UX-Request": "1", "Origin": "https://outside.invalid"}
            assert client.post("/api/reviews", json=payload, headers=headers).status_code == 403
            headers = {"X-UX-Request": "1", "Origin": str(client.base_url).rstrip("/")}
            response = client.post("/api/reviews", json=payload, headers=headers)
            assert response.status_code == 202 and response.json()["created"]
            assert not client.post("/api/reviews", json=payload, headers=headers).json()["created"]
            assert len(client.get("/api/reviews").json()["jobs"]) == 1
            bad = {**payload, "repo": "file:///etc/passwd"}
            assert client.post("/api/reviews", json=bad, headers=headers).status_code == 400
            assert client.head("/deploys.html").content == b""
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
