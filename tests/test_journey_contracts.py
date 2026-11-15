import json
from types import SimpleNamespace

from test_orchestration import FakeRuntime, good_judgement

from ai_ux.agent import judge_run
from ai_ux.evidence import Evidence
from ai_ux.journey import combine_verification, explore
from ai_ux.report import render
from ai_ux.schema import Action, Assertion, Phase, PhaseAssessment, Task
from ai_ux.verifier import verify


async def test_retry_does_not_reuse_old_success_events():
    class Runtime:
        evidence = SimpleNamespace(
            events=[
                {
                    "kind": "product_action",
                    "data": {"kind": "result", "status": "completed", "text": "nod · completed"},
                }
            ]
        )

    assertion = Assertion(id="again", kind="action_completed", action_name="nod")
    assert (await verify(Runtime(), [assertion]))["task_outcome"] == "passed"
    assert (await verify(Runtime(), [assertion], events=[]))["task_outcome"] == "failed"


async def test_setup_failure_blocks_transition_but_still_runs_recovery(tmp_path):
    evidence = Evidence(tmp_path / "run")
    task = Task(
        id="x",
        goal="检查设置失败后的恢复",
        max_steps=8,
        phases=[
            Phase(
                id="setup",
                role="setup",
                goal="准备功能场景",
                assertions=[Assertion(id="setup", kind="element_visible", selector="#private")],
            ),
            Phase(id="switch", role="transition", goal="切换另一页面"),
            Phase(
                id="recover",
                role="recovery",
                goal="恢复原来状态",
                assertions=[Assertion(id="recover", kind="element_visible", selector="#stop")],
            ),
        ],
    )

    class Runtime(FakeRuntime):
        async def inspect_assertion(self, assertion):
            return assertion.id == "recover"

    class Model:
        seen = []

        async def structured(self, system, prompt, schema, **kwargs):
            data = json.loads(prompt)
            self.seen.append(data["current_phase"]["id"])
            assert "#private" not in prompt
            return Action(kind="done", rationale="记录状态"), {}

    runtime = Runtime(None, None, task, evidence)
    model = Model()
    phases = []
    assert (
        await explore(
            model,
            runtime,
            task,
            evidence,
            "system",
            "scope",
            phases=phases,
            invariants=[],
            log=lambda *a, **k: None,
        )
        == "journey_incomplete"
    )
    assert model.seen == ["setup", "recover"]
    assert phases[1]["status"] == "blocked"
    final = combine_verification(await verify(runtime, []), phases, [])
    assert final["task_outcome"] == "failed" and final["recovery_outcome"] == "passed"


async def test_auto_checkpoint_does_not_require_model_to_wait_for_completion(tmp_path):
    evidence = Evidence(tmp_path / "run")
    task = Task(
        id="x",
        goal="在执行中切换阶段",
        phases=[
            Phase(
                id="active",
                role="setup",
                goal="确认正在执行",
                auto_advance=True,
                assertions=[Assertion(id="running", kind="text", selector="#state", contains="running")],
            )
        ],
    )
    runtime = FakeRuntime(None, None, task, evidence)

    class Model:
        async def structured(self, *args, **kwargs):
            raise AssertionError("checkpoint already reached; must not spend an LLM turn")

    phases = []
    assert (
        await explore(
            Model(),
            runtime,
            task,
            evidence,
            "system",
            "scope",
            phases=phases,
            invariants=[],
            log=lambda *a, **k: None,
        )
        == "journey_completed"
    )
    assert phases[0]["status"] == "checkpoint_reached"


async def test_judge_keeps_only_supplied_phase_ids_and_evidence(tmp_path):
    e = Evidence(tmp_path / "run")
    e.event("start")
    (e.root / "screenshots/x.png").write_bytes(b"x")
    e.observations = [
        {"step": 0, "t_ms": 0, "url": "https://example.test", "screenshot": "screenshots/x.png"}
    ]
    e.write("browser-environment.json", {"device": "android", "is_mobile": True})
    task = Task(
        id="x",
        goal="复核连续流程",
        phases=[Phase(id="setup", goal="启动原有任务"), Phase(id="retry", role="retry", goal="重新执行任务")],
    )

    class Model:
        config = SimpleNamespace(primary=SimpleNamespace(name="p", video_chunk_seconds=None), fallback=None)

        async def structured(self, system, prompt, schema, **kwargs):
            result = good_judgement()
            result.issues = []
            result.phase_assessments = [
                PhaseAssessment(phase_id=key, outcome="passed", observations=["visible"], evidence_refs=[ref])
                for key, ref in [
                    ("setup", "screenshots/x.png"),
                    ("setup", "events.jsonl"),
                    ("unknown", "events.jsonl"),
                    ("retry", "invented.png"),
                ]
            ]
            return result, {}

    result, _ = await judge_run(Model(), e, task, {"phases": [{"id": "setup"}]}, {"status": "disabled"})
    assert len(result["phase_assessments"]) == 1
    assert len(json.loads((e.root / "judge-rejected.json").read_text())) == 3
    assert any("真" in text for text in result["limitations"])
    assert any("所有阶段" in text for text in result["limitations"])


def test_report_keeps_initial_failure_and_recovery_visible(tmp_path):
    phases = [
        {
            "id": key,
            "role": role,
            "goal": goal,
            "status": "explorer_done",
            "verification": {"task_outcome": status},
        }
        for key, role, goal, status in [
            ("switch", "transition", "<script>failure</script>", "failed"),
            ("retry", "retry", "恢复后再次开播", "passed"),
        ]
    ]
    run = {
        "run_id": "x",
        "task_id": "handoff-android",
        "attempt": 1,
        "goal": "连续场景",
        "duration_ms": 20,
        "stop_reason": "journey_completed",
        "phase_results": phases,
        "browser": {
            "device": "android",
            "engine": "chromium",
            "viewport": {"width": 412, "height": 839},
            "limitations": ["模拟环境，未验证真机"],
        },
        "verification": {"task_outcome": "failed", "recovery_outcome": "passed"},
    }
    render(
        tmp_path,
        {
            "batch_id": "test",
            "target_url": "https://example.test",
            "runs": [run],
            "issues": [],
            "coverage_limitations": ["<missing>状态切换入口未覆盖"],
        },
    )
    body = (tmp_path / "report.html").read_text()
    assert "<script>failure" not in body and "&lt;script&gt;" in body
    assert "failed" in body and "passed" in body and "未复核" in body
    assert "模拟环境" in body and "x/phases/retry.json" in body
    assert "连续流程与恢复验收" in (tmp_path / "report.md").read_text()
    assert "未完成的请求覆盖" in body and "&lt;missing&gt;状态切换入口未覆盖" in body
