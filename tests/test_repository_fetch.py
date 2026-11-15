import json
import subprocess
from pathlib import Path

import pytest

from ai_ux import discovery, repository
from ai_ux.repository import prepare_repository


def fake_checkout(path):
    path.mkdir()
    (path / "README.md").write_text("# Product\nAn example product.\n")


def test_network_retry_discards_partial_checkout_and_publishes_complete_repo(tmp_path, monkeypatch):
    real_run = subprocess.run
    attempts, events, sleeps = [], [], []

    def run(cmd, **kwargs):
        if "clone" not in cmd:
            return real_run(cmd, **kwargs)
        checkout = Path(cmd[-1])
        attempts.append((checkout, cmd))
        fake_checkout(checkout)
        if len(attempts) == 1:
            (checkout / "partial.js").write_text("incomplete")
            raise subprocess.CalledProcessError(
                128,
                cmd,
                stderr=b"RPC failed; curl 56 connection reset. https://user:secret@host/x token=private",
            )
        assert not attempts[0][0].exists()
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert kwargs["env"]["GIT_HTTP_LOW_SPEED_TIME"] == "30"
        assert kwargs["env"]["GIT_HTTP_LOW_SPEED_LIMIT"] == "1000"
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(repository.subprocess, "run", run)
    monkeypatch.setattr(repository, "sleep", sleeps.append)
    monkeypatch.setenv("GIT_HTTP_LOW_SPEED_TIME", "5")
    root = tmp_path / "checkout"
    repo = prepare_repository("https://example.com/project", root, on_progress=events.append)
    assert repo.root == root and repo.fetch == {"attempts": 2, "transport": "HTTP/1.1"}
    assert repo.files == ["README.md"] and not (root / "partial.js").exists()
    assert "http.version=HTTP/1.1" in attempts[1][1]
    assert not any("sslVerify=false" in arg for arg in attempts[1][1])
    assert len(sleeps) == 1 and not list(tmp_path.glob(".clone-*"))
    assert [e["status"] for e in events] == ["running", "failed", "running", "completed"]
    assert events[1]["retrying"] and "secret" not in json.dumps(events)
    assert "private" not in json.dumps(events)


@pytest.mark.parametrize(
    "stderr,code,retries",
    [
        (b"fatal: Authentication failed", "access_denied", 1),
        (b"fatal: Repository not found", "repository_not_found", 1),
        (b"fatal: repository 'https://example.com/nope/' not found", "repository_not_found", 1),
        (b"fatal: Remote branch missing not found in upstream origin", "ref_not_found", 1),
        (b"SSL certificate problem: certificate verify failed", "certificate", 1),
        (b"fatal: could not create work tree dir: No space left on device", "filesystem", 1),
        (b"fatal: unexpected custom helper error token=secret", "git_failed", 1),
        (b"fatal: early EOF", "network", 2),
        (
            b"fatal: Operation too slow. Less than 1000 bytes/sec transferred the last 5 seconds",
            "low_speed_timeout",
            2,
        ),
        (b"fatal: unable to access: The requested URL returned error: 503", "network", 2),
    ],
)
def test_failure_is_classified_bounded_and_does_not_leak_stderr(tmp_path, monkeypatch, stderr, code, retries):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        fake_checkout(Path(cmd[-1]))
        raise subprocess.CalledProcessError(128, cmd, stderr=stderr)

    monkeypatch.setattr(repository.subprocess, "run", run)
    monkeypatch.setattr(repository, "sleep", lambda _: None)
    destination = tmp_path / "checkout"
    with pytest.raises(ValueError, match=code) as error:
        prepare_repository("https://example.com/project", destination)
    assert len(calls) == retries and f"{retries} 次" in str(error.value)
    assert "secret" not in str(error.value)
    assert not destination.exists() and not list(tmp_path.glob(".clone-*"))


@pytest.mark.parametrize(
    "failure,code,count",
    [(FileNotFoundError(), "git_unavailable", 1), (subprocess.TimeoutExpired("git", 120), "timeout", 2)],
)
def test_missing_git_and_timeouts_have_distinct_outcomes(tmp_path, monkeypatch, failure, code, count):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        raise failure

    monkeypatch.setattr(repository.subprocess, "run", run)
    monkeypatch.setattr(repository, "sleep", lambda _: None)
    with pytest.raises(ValueError, match=code):
        prepare_repository("git@example.com:project.git", tmp_path / "checkout")
    assert len(calls) == count
    assert all("http.version=HTTP/1.1" not in cmd for cmd in calls)


def test_existing_clone_destination_is_never_deleted_or_replaced(tmp_path):
    destination = tmp_path / "checkout"
    fake_checkout(destination)
    with pytest.raises(ValueError, match="already exists"):
        prepare_repository("https://example.com/project", destination)
    assert (destination / "README.md").read_text().startswith("# Product")


async def test_discovery_retains_repository_failure_evidence_without_opening_browser(tmp_path, monkeypatch):
    def failing_repo(*args, on_progress, **kwargs):
        on_progress({"status": "failed", "attempt": 1, "code": "access_denied"})
        raise ValueError("Git clone 失败 [access_denied]：无访问权限")

    monkeypatch.setattr(discovery, "prepare_repository", failing_repo)
    with pytest.raises(ValueError, match="access_denied"):
        await discovery.discover("https://example.com/project", "https://example.com/", output=tmp_path)
    root = next(tmp_path.iterdir()) / "discovery"
    events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
    assert [e["kind"] for e in events] == ["stage", "repository_fetch", "stage_error"]
    assert (root / "replay.html").is_file() and (root / "manifest.json").is_file()
    assert not (root.parent / "checkout").exists()
