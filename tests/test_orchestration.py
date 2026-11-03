import json
from types import SimpleNamespace

import pytest
from test_llm import providers

from ai_ux import agent
from ai_ux.evidence import Evidence
from ai_ux.llm import LLMError
from ai_ux.schema import Action, App, Browser, Config, Finding, Judgement, Task


def config(tmp_path, monkeypatch):
    return Config(
        app=App(
            name="Fixture", base_url="http://localhost/", allowed_hosts=["localhost"], environment="local"
        ),
        models=providers(monkeypatch),
        browser=Browser(video=False),
        output_dir=str(tmp_path / "runs"),
        tasks=[Task(id="flow", goal="Complete the form")],
    )


def good_judgement():
    return Judgement(
        summary="Observed",
        task_assessment="passed",
        issues=[
            Finding(
                category="feedback",
                severity="P2",
                title="Completion feedback is unclear",
                observations=["No visible confirmation"],
                inference="Users may repeat the action",
                recommendation="Add status",
                reproduction_steps=["Submit"],
                evidence_refs=["screenshots/step-000.png", "events.jsonl"],
                confidence="high",
                acceptance=["Status visible"],
            )
        ],
    )


class FakeRuntime:
    start_error = None
    action_ok = True

    def __init__(self, app, browser, task, evidence):
        self.evidence = evidence
        self.page = SimpleNamespace(is_closed=lambda: False)
        self.video_info = {"status": "disabled"}
        self.cleanup = {}

    async def start(self):
        self.evidence.event("browser_started")
        if self.start_error:
            raise self.start_error

    async def observe(self, step):
        screenshot = f"screenshots/step-{step:03}.png"
        (self.evidence.root / screenshot).write_bytes(b"image")
        result = {
            "step": step,
            "t_ms": self.evidence.ms(),
            "url": "http://localhost/",
            "nodes": [],
            "text": "Form",
            "screenshot": screenshot,
            "evidence_ref": f"observations/step-{step:03}.json",
        }
        self.evidence.observations.append(result)
        self.evidence.write(result["evidence_ref"], result)
        return result

    async def perform(self, action):
        return {"ok": self.action_ok, "duration_ms": 2}

    async def inspect_assertion(self, assertion):
        return True

    async def finish(self):
        self.cleanup = {"status": "not_applicable"}


class FakeLLM:
    decisions = []
    judgement_error = None
    calls_seen = []

    def __init__(self, *args, **kwargs):
        self.config = args[0]
        self.calls = []
        self.closed = False

    async def structured(self, system, prompt, schema, **kwargs):
        self.calls_seen.append((schema, json.loads(prompt) if prompt.startswith("{") else prompt, kwargs))
        call = {"provider": "test", "model": "test", "status": "ok"}
        self.calls.append(call)
        if schema == Action:
            action = self.decisions.pop(0) if self.decisions else Action(kind="done", rationale="Complete")
            return action, call
        if self.judgement_error:
            raise self.judgement_error
        return good_judgement(), call

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_engine(monkeypatch):
    FakeRuntime.start_error = None
    FakeRuntime.action_ok = True
    FakeLLM.decisions = []
    FakeLLM.judgement_error = None
    FakeLLM.calls_seen = []
    monkeypatch.setattr(agent, "BrowserRuntime", FakeRuntime)
    monkeypatch.setattr(agent, "LLM", FakeLLM)


async def test_run_is_checkpointed_verified_and_source_hidden(tmp_path, monkeypatch, fake_engine):
    cfg = config(tmp_path, monkeypatch)
    cfg.tasks[0].assertions = []
    result = await agent.run_task(cfg, cfg.tasks[0], tmp_path, 1, log=lambda *a, **k: None)
    assert result["stop_reason"] == "explorer_done" and result["judge_status"] == "completed"
    assert result["verification"]["task_outcome"] == "not_verified"
    assert result["judgement"]["issues"][0]["status"] == "needs_review"
    root = tmp_path / result["run_id"]
    assert (root / "manifest.json").is_file() and (root / "run.json").is_file()
    explorer = next(data for schema, data, _ in FakeLLM.calls_seen if schema == Action)
    assert "assertions" not in explorer["task"] and "source_repo" not in json.dumps(explorer)
    assert "room" not in explorer["policy"].lower()


@pytest.mark.parametrize("mode,reason", [("runtime", "runtime_error"), ("timeout", "task_timeout")])
async def test_failure_still_writes_verification_cleanup_and_manifest(
    tmp_path, monkeypatch, fake_engine, mode, reason
):
    cfg = config(tmp_path, monkeypatch)
    FakeRuntime.start_error = RuntimeError("browser failed") if mode == "runtime" else TimeoutError()
    result = await agent.run_task(cfg, cfg.tasks[0], tmp_path, 1, log=lambda *a, **k: None)
    assert result["stop_reason"] == reason
    assert result["cleanup"]["status"] == "not_applicable"
    root = tmp_path / result["run_id"]
    assert (root / "verification.json").is_file() and (root / "manifest.json").is_file()


async def test_three_action_failures_stop_without_claiming_done(tmp_path, monkeypatch, fake_engine):
    cfg = config(tmp_path, monkeypatch)
    FakeRuntime.action_ok = False
    FakeLLM.decisions = [Action(kind="wait", rationale="try") for _ in range(4)]
    result = await agent.run_task(cfg, cfg.tasks[0], tmp_path, 1, log=lambda *a, **k: None)
    assert result["stop_reason"] == "consecutive_action_errors"
    assert len(FakeLLM.decisions) == 1


async def test_step_limit_and_judge_failure_are_separate(tmp_path, monkeypatch, fake_engine):
    cfg = config(tmp_path, monkeypatch)
    cfg.tasks[0].max_steps = 1
    FakeLLM.decisions = [Action(kind="wait", rationale="try")]
    FakeLLM.judgement_error = LLMError("overload")
    result = await agent.run_task(cfg, cfg.tasks[0], tmp_path, 1, log=lambda *a, **k: None)
    assert result["stop_reason"] == "step_limit" and result["judge_status"] == "failed"
    assert "overload" in result["judge_error"]


async def test_rejudge_archives_prior_result_and_updates_batch(tmp_path, monkeypatch, fake_engine):
    cfg = config(tmp_path, monkeypatch)
    root, report = await agent.evaluate(cfg)
    runroot = root / report["runs"][0]["run_id"]
    prior = json.loads((runroot / "run.json").read_text())
    prior["source_mapping"] = {"status": "completed", "mappings": [{"issue_index": 99}]}
    (runroot / "run.json").write_text(json.dumps(prior))
    original = (runroot / "run.json").read_text()
    result = await agent.rejudge(cfg, runroot)
    assert result["judge_status"] == "completed"
    assert "source_mapping" not in result
    assert next((runroot / "judge-history").glob("*/run.json")).read_text() == original
    assert json.loads((root / "report.json").read_text())["runs"][0]["run_id"] == result["run_id"]
    with pytest.raises(ValueError, match="unknown task"):
        await agent.evaluate(cfg, ["unknown"])


async def test_judge_rejects_unsupplied_and_missing_evidence(tmp_path, monkeypatch):
    e = Evidence(tmp_path / "e")
    e.event("initial")
    (e.root / "screenshots/x.png").write_bytes(b"x")
    (e.root / "secret.json").write_text("{}")
    e.observations = [
        {"step": 0, "t_ms": 0, "url": "https://example.test", "screenshot": "screenshots/x.png"}
    ]

    class Invalid:
        config = SimpleNamespace(primary=SimpleNamespace(name="p", video_chunk_seconds=None), fallback=None)

        async def structured(self, *args, **kw):
            j = good_judgement()
            j.issues[0].evidence_refs = ["secret.json"]
            return j, {}

    result, _ = await agent.judge_run(Invalid(), e, Task(id="x", goal="task"), {}, {"status": "disabled"})
    assert result["issues"] == [] and json.loads((e.root / "judge-rejected.json").read_text())


async def test_judge_uses_native_video_chunks_before_fallback_synthesis(tmp_path, monkeypatch):
    e = Evidence(tmp_path / "e")
    e.event("initial")
    (e.root / "screenshots/step-000.png").write_bytes(b"x")
    (e.root / "review.mp4").write_bytes(b"video")
    e.observations = [
        {"step": 0, "t_ms": 0, "url": "https://example.test", "screenshot": "screenshots/step-000.png"}
    ]

    async def clips(*args):
        p = e.root / "clip.mp4"
        p.write_bytes(b"video")
        return [{"path": p, "start_s": 0, "end_s": 3, "padding_seconds": 1}]

    monkeypatch.setattr(agent, "split_review_video", clips)
    p = SimpleNamespace(name="p", video_chunk_seconds=None)
    b = SimpleNamespace(name="b", video_chunk_seconds=4, model_copy=lambda **kw: b)
    seen = []

    class Model:
        config = SimpleNamespace(primary=p, fallback=b)

        async def structured(self, system, prompt, schema, **kw):
            seen.append((schema, kw))
            if kw["providers"][0] is p:
                raise LLMError("unavailable")
            if schema == agent.VideoNotes:
                return agent.VideoNotes(observations=["button"], limitations=[]), {}
            return good_judgement(), {"provider": "b"}

    result, call = await agent.judge_run(
        Model(), e, Task(id="x", goal="task"), {}, {"status": "ready", "review": {"path": "review.mp4"}}
    )
    assert call["provider"] == "b"
    assert seen[1][1]["video"].name == "clip.mp4"
    assert "video" not in seen[2][1]
    assert json.loads((e.root / "video-notes.json").read_text())[0]["padding_seconds"] == 1


def test_source_version_nonexistent_path_returns_none(tmp_path):
    assert agent.source_version(None) is None
    assert agent.source_version(str(tmp_path / "missing")) is None
