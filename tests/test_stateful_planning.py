import json

import pytest
from pydantic import ValidationError
from test_browser_integration import local_site
from test_generic import make_repository
from test_llm import providers

from ai_ux.discovery import PlannedPhase, PlannedTask, StatefulProjectPlan, discover
from ai_ux.llm import LLM
from ai_ux.schema import Browser, load_config


def plan():
    return StatefulProjectPlan(
        product_name="Demo",
        product_summary="检查跨页恢复",
        tasks=[
            PlannedTask(
                id="handoff",
                goal="在执行中切换页面并恢复",
                expected_outcome="同一会话可以重新开始",
                dimension="recovery",
                rationale="已观察的页面入口",
                phases=[
                    PlannedPhase(id=role, role=role, goal=goal, expected_outcome=expected)
                    for role, goal, expected in [
                        ("setup", "启动一个可中断任务", "任务实际执行中"),
                        ("transition", "切页后启动另一项", "新任务实际完成"),
                        ("recovery", "返回停止原先任务", "原任务停止并可继续"),
                        ("retry", "重新启动目标任务", "本次目标实际完成"),
                    ]
                ],
            )
        ],
    )


async def test_discovery_persists_phases_and_independent_mobile_matrix(tmp_path, monkeypatch):
    captured = []

    async def structured(self, system, prompt, schema, **kwargs):
        captured.append((schema, json.loads(prompt)))
        return plan(), {"provider": "test"}

    monkeypatch.setattr(LLM, "structured", structured)
    root = make_repository(tmp_path / "repo")
    async with local_site() as url:
        project, config, _ = await discover(
            str(root),
            url,
            output=tmp_path / "out",
            models=providers(monkeypatch),
            browser=Browser(video=False),
            task_count=1,
            devices=["desktop", "android"],
            coverage="stateful",
            task_steps=24,
        )
    assert captured[0][0] is StatefulProjectPlan
    assert captured[0][1]["devices"] == ["desktop", "android"]
    assert [t.id for t in config.tasks] == ["handoff-desktop", "handoff-android"]
    assert all(len(t.phases) == 4 for t in config.tasks)
    assert config.tasks[0].phases[0] is not config.tasks[1].phases[0]
    saved = load_config(project / "project.yaml")
    assert saved.tasks[1].browser.device == "android"
    assert saved.tasks[1].invariants[0].required is True
    assert json.loads((project / "plan.json").read_text())["stateful_task_count"] == 2


def test_planner_cannot_silently_omit_stateful_coverage():
    value = plan().model_dump()
    value["tasks"][0]["phases"] = []
    with pytest.raises(ValidationError):
        StatefulProjectPlan.model_validate(value)
    value["stateful_limitation"] = "页面没有可验证的跨页入口，未覆盖状态切换。"
    assert StatefulProjectPlan.model_validate(value).stateful_limitation


async def test_job_manager_passes_selected_device_and_coverage(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from ai_ux import agent, discovery
    from ai_ux.jobs import JobManager
    from ai_ux.store import Store

    seen = []

    async def fake_discover(repo, url, **kwargs):
        seen.append(kwargs)
        return tmp_path, SimpleNamespace(), None

    async def fake_evaluate(*args, **kwargs):
        return root / "batch", {"runs": []}

    monkeypatch.setattr(discovery, "discover", fake_discover)
    monkeypatch.setattr(agent, "evaluate", fake_evaluate)
    store = Store(tmp_path / "store")
    manager = JobManager(store, workers=0)
    root = tmp_path / "store/jobs/example"
    root.mkdir(parents=True)
    result = await manager.execute(
        {
            "id": "example",
            "request": {
                "repo": "https://github.com/o/r.git",
                "url": "https://example.com",
                "devices": ["android"],
                "coverage": "stateful",
                "max_steps": 24,
            },
        },
        root,
    )
    assert result["status"] == "completed"
    assert seen[0]["devices"] == ["android"] and seen[0]["coverage"] == "stateful"
