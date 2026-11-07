import json

import pytest
from pydantic import ValidationError

from ai_ux.browser import BrowserRuntime
from ai_ux.evidence import Evidence
from ai_ux.policy import Policy, safe_url, scrub
from ai_ux.report import aggregate, render
from ai_ux.schema import Action, App, Assertion, Browser, Task
from ai_ux.verifier import check_events


def app():
    return App(
        name="demo", base_url="https://duck.qbox.net/", allowed_hosts=["duck.qbox.net"], adapter="duckking"
    )


def test_task_public_view_does_not_leak_assertions_or_selectors():
    task = Task(
        id="test",
        goal="click a button",
        assertions=[Assertion(id="secret", kind="text", selector="#private-locator", contains="secret")],
    )
    assert "private-locator" not in json.dumps(task.public())
    assert "assertions" not in task.public()


def test_policy_blocks_external_schemes_credentials_and_other_rooms():
    p = Policy(app())
    p.room = "7"
    for url in [
        "https://evil.test",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://user:pass@duck.qbox.net/",
        "https://duck.qbox.net/?room=1",
    ]:
        with pytest.raises(ValueError):
            p.check_url(url)
    assert p.check_url("/scenarios.html?room=7").endswith("room=7")
    with pytest.raises(ValueError):
        p.check_control({"name": "＋ 新建房间", "dom_id": "create-room"})


def test_action_validation_and_config_production_guard():
    with pytest.raises(ValidationError):
        Action(kind="click", rationale="x")
    with pytest.raises(ValidationError):
        Action(kind="press", key="Meta+L", rationale="x")
    with pytest.raises(ValidationError):
        Action(kind="wait", seconds=500, rationale="x")
    with pytest.raises(ValidationError):
        App(name="x", base_url="https://x", allowed_hosts=["x"], environment="production_evaluation")


def test_redaction_preserves_usage_and_removes_secrets():
    result = scrub(
        {
            "authorization": "abc",
            "access_token": "token",
            "prompt_tokens": 100,
            "total_tokens": 120,
            "nested": {"session_id": "x"},
        }
    )
    assert result["prompt_tokens"] == 100 and result["total_tokens"] == 120
    assert result["authorization"] == "[REDACTED]" and result["access_token"] == "[REDACTED]"
    assert "secret" not in safe_url("https://u:secret@duck.qbox.net/?token=secret&room=7")


def test_evidence_rejects_path_traversal_and_missing(tmp_path):
    evidence = Evidence(tmp_path / "run")
    (tmp_path / "secret").write_text("no")
    for ref in ["../secret", "missing.png", str(tmp_path / "secret")]:
        with pytest.raises(ValueError):
            evidence.resolve(ref)


def test_initial_websocket_history_never_proves_current_attempt(tmp_path):
    evidence = Evidence(tmp_path / "run")
    runtime = BrowserRuntime(app(), Browser(), Task(id="test", goal="nod"), evidence)
    old = {
        "type": "state",
        "events": [
            {"id": 1, "action_id": "old", "kind": "result", "status": "completed", "text": "nod · completed"}
        ],
    }
    runtime._frame(json.dumps(old), "received", "/ws")
    assertion = Assertion(id="nod", kind="action_completed", action_name="nod")
    assert check_events(assertion, evidence.events) is False
    old["events"].append(
        {"id": 2, "action_id": "new", "kind": "result", "status": "completed", "text": "nod · completed"}
    )
    runtime._frame(json.dumps(old), "received", "/ws")
    assert check_events(assertion, evidence.events) is True
    assert len([e for e in evidence.events if e["kind"] == "product_action"]) == 1


def test_action_success_does_not_prove_visible_before_action():
    events = [
        {"kind": "product_action", "t_ms": 100, "data": {"kind": "action", "text": "开始动作 · nod"}},
        {"kind": "display_state", "t_ms": 300, "data": {"model": "official"}},
        {
            "kind": "product_action",
            "t_ms": 500,
            "data": {"kind": "result", "status": "completed", "text": "nod · completed"},
        },
    ]
    assert check_events(Assertion(id="done", kind="action_completed", action_name="nod"), events)
    assert not check_events(Assertion(id="seen", kind="model_before_action"), events)


def test_streaming_transcript_before_release_is_preserved():
    events = [
        {
            "kind": "voice_event",
            "direction": "received",
            "t_ms": 500,
            "data": {"type": "transcript", "role": "user", "text": "点头"},
        },
        {"kind": "audio_input_end", "t_ms": 700},
    ]
    assert check_events(
        Assertion(id="heard", kind="voice_transcript", source="cloud", contains="点头"), events
    )


def test_report_displays_independent_asr_and_escapes_transcript(tmp_path):
    run = {
        "task_id": "voice",
        "run_id": "voice-01",
        "attempt": 1,
        "goal": "Listen",
        "stop_reason": "explorer_done",
        "duration_ms": 100,
        "judge_status": "completed",
        "speech_review": {"status": "completed", "model": "asr-1.0", "transcript": "<script>unsafe</script>"},
    }
    render(tmp_path, {"batch_id": "test", "target_url": "https://duck.qbox.net", "runs": [run], "issues": []})
    body = (tmp_path / "report.html").read_text()
    assert "<script>" not in body and "&lt;script&gt;" in body
    assert "voice-01/response.wav" in body and "voice-01/speech-review.json" in body


def test_dedupe_does_not_confirm_single_run_and_html_escapes_model_output(tmp_path):
    issue = {
        "title": "<script>bad</script>",
        "category": "feedback",
        "severity": "P2",
        "confidence": "high",
        "observations": ["<script>x</script>"],
        "inference": "<img onerror=alert(1)>",
        "recommendation": "fix",
        "reproduction_steps": ["click"],
        "acceptance": ["ok"],
        "evidence_refs": ["screenshots/x.png"],
    }
    run = {
        "task_id": "task",
        "run_id": "a",
        "attempt": 1,
        "goal": "task",
        "stop_reason": "done",
        "duration_ms": 100,
        "judgement": {"summary": "<script>oops</script>", "limitations": [], "issues": [issue]},
    }
    one = aggregate([run])
    assert one[0]["status"] == "needs_review"
    two = aggregate([run, {**run, "run_id": "b", "attempt": 2}])
    assert two[0]["status"] == "candidate"
    render(
        tmp_path, {"batch_id": "test", "target_url": "https://duck.qbox.net", "runs": [run], "issues": one}
    )
    assert "<script>" not in (tmp_path / "report.html").read_text()
