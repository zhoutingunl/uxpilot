import json

import pytest
from test_browser_integration import local_site

from ai_ux.browser import BrowserRuntime
from ai_ux.evidence import Evidence
from ai_ux.journey import combine_verification, explore
from ai_ux.schema import Action, App, Assertion, Browser, Phase, Task
from ai_ux.verifier import verify


def handoff_fixture(mode):
    return (
        (
            """<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
    <script>
    const mode=MODE, theatre=location.pathname==='/demo';
    sessionStorage.setItem('room',sessionStorage.getItem('room')||'owned-room');
    document.addEventListener('DOMContentLoaded',()=>{
      document.body.innerHTML=theatre?'<button id="play">Play</button><p id="status">idle</p>':'<button id="start">Start director</button><button id="stop">Stop task</button><a id="demo" href="/demo">Demo</a><p id="state">ready</p>';
      if(theatre) document.querySelector('#play').onclick=()=>{
        const locked=sessionStorage.getItem('backend')==='functional';
        const running=sessionStorage.getItem('running')==='true';
        const blocked=locked&&(mode==='broken'||(mode==='recoverable'&&running));
        document.querySelector('#status').textContent=blocked?'请使用当前功能场景工具':'playing';
        if(!blocked)sessionStorage.setItem('backend','theatre');
      };
      else {
        document.querySelector('#start').onclick=()=>{sessionStorage.setItem('backend','functional');sessionStorage.setItem('running','true');document.querySelector('#state').textContent='running'};
        document.querySelector('#stop').onclick=()=>{sessionStorage.setItem('running','false');document.querySelector('#state').textContent='stopped'};
        document.querySelector('#demo').onclick=()=>sessionStorage.setItem('link-clicked','yes');
      }
    });</script>"""
        )
        .replace("MODE", json.dumps(mode))
        .encode()
    )


def handoff_task():
    return Task(
        id="handoff",
        goal="运行中切页并验证停止后的恢复",
        max_steps=16,
        phases=[
            Phase(
                id="start",
                role="setup",
                goal="启动四鸭任务",
                assertions=[Assertion(id="started", kind="text", selector="#state", contains="running")],
            ),
            Phase(
                id="switch",
                role="transition",
                goal="中途切到节目并开播",
                assertions=[Assertion(id="playing", kind="text", selector="#status", contains="playing")],
            ),
            Phase(
                id="stop",
                role="recovery",
                goal="返回停止原任务",
                assertions=[Assertion(id="stopped", kind="text", selector="#state", contains="stopped")],
            ),
            Phase(
                id="retry",
                role="retry",
                goal="回节目重新开播",
                assertions=[Assertion(id="restarted", kind="text", selector="#status", contains="playing")],
            ),
        ],
    )


class ObservedActions:
    def __init__(self):
        self.steps = {}
        self.payloads = []

    async def structured(self, system, prompt, schema, **kwargs):
        data = json.loads(prompt)
        self.payloads.append(data)
        phase = data["current_phase"]["id"]
        number = self.steps.get(phase, 0)
        self.steps[phase] = number + 1
        sequence = {
            "start": ["start"],
            "switch": ["demo", "play"],
            "stop": ["back", "stop"],
            "retry": ["demo", "play"],
        }
        if number >= len(sequence[phase]):
            return Action(kind="done", rationale="已检查本阶段结果"), {}
        action = sequence[phase][number]
        if action == "back":
            return Action(kind="back", rationale="返回同一会话"), {}
        node = next(n for n in data["observation"]["nodes"] if n["dom_id"] == action)
        return Action(kind="click", target=node["id"], rationale="操作当前可见入口"), {}


@pytest.mark.parametrize(
    "mode,outcome,retry",
    [("broken", "failed", "failed"), ("recoverable", "failed", "passed"), ("healthy", "passed", "passed")],
)
async def test_stateful_handoff_keeps_failure_even_after_recovery(tmp_path, mode, outcome, retry):
    task = handoff_task()
    evidence = Evidence(tmp_path / "run")
    model = ObservedActions()
    phases, invariants = [], []
    async with local_site(handoff_fixture(mode)) as url:
        runtime = BrowserRuntime(
            App(name="fixture", base_url=url, allowed_hosts=["127.0.0.1"], environment="local"),
            Browser(video=False),
            task,
            evidence,
        )
        try:
            await runtime.start()
            original_page = runtime.page
            status = await explore(
                model,
                runtime,
                task,
                evidence,
                "system",
                "owned session",
                phases=phases,
                invariants=invariants,
                log=lambda *a, **kw: None,
            )
            assert status == "journey_completed"
            assert runtime.page is original_page
            assert await runtime.page.evaluate('sessionStorage.getItem("room")') == "owned-room"
            assert await runtime.page.evaluate('sessionStorage.getItem("link-clicked")') == "yes"
            result = combine_verification(await verify(runtime, []), phases, invariants)
            assert result["task_outcome"] == outcome
            assert phases[-1]["verification"]["task_outcome"] == retry
            if mode == "recoverable":
                assert phases[1]["verification"]["task_outcome"] == "failed"
                assert result["recovery_outcome"] == "passed"
            for payload in model.payloads:
                assert "assertions" not in payload["task"] and "phases" not in payload["task"]
                assert "selector" not in json.dumps(payload["journey"])
                assert payload["visited_pages"]
                if payload["current_phase"]["id"] == "stop":
                    assert payload["phase_history"][0]["visited_urls"] == [url]
            assert len(list((evidence.root / "phases").glob("*.json"))) == 4
        finally:
            await runtime.finish()


async def test_transient_mobile_overflow_is_not_erased_by_final_recovery(tmp_path):
    task = Task(
        id="overflow",
        goal="输入后恢复布局",
        max_steps=4,
        invariants=[Assertion(id="width", kind="no_horizontal_overflow")],
    )
    evidence = Evidence(tmp_path / "run")
    html = b"""<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><button id="break">Break</button><button id="fix">Fix</button><script>document.querySelector('#break').onclick=()=>document.body.style.width='2000px';document.querySelector('#fix').onclick=()=>document.body.style.width='auto';</script>"""

    class Model:
        steps = 0

        async def structured(self, system, prompt, schema, **kwargs):
            data = json.loads(prompt)
            name = ["break", "fix", None][self.steps]
            self.steps += 1
            if name is None:
                return Action(kind="done", rationale="结束"), {}
            node = next(n for n in data["observation"]["nodes"] if n["dom_id"] == name)
            return Action(kind="click", target=node["id"], rationale="观察布局变化"), {}

    async with local_site(html) as url:
        runtime = BrowserRuntime(
            App(name="fixture", base_url=url, allowed_hosts=["127.0.0.1"], environment="local"),
            Browser(device="android", video=False),
            task,
            evidence,
        )
        try:
            await runtime.start()
            phases = []
            checks = []
            await explore(
                Model(),
                runtime,
                task,
                evidence,
                "system",
                "scope",
                phases=phases,
                invariants=checks,
                log=lambda *a, **kw: None,
            )
            assert await runtime.inspect_assertion(task.invariants[0]) is True
            assert any(c["status"] == "failed" for c in checks)
            assert combine_verification(await verify(runtime, []), phases, checks)["task_outcome"] == "failed"
        finally:
            await runtime.finish()
