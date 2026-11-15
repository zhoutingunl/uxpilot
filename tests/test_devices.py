from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from pydantic import ValidationError
from test_browser_integration import local_site

from ai_ux.browser import BrowserRuntime
from ai_ux.devices import device_browser, expand_tasks
from ai_ux.evidence import Evidence
from ai_ux.schema import Action, App, Assertion, Browser, EvaluationOptions, Task


@pytest.mark.parametrize("device", ["desktop", "android", "iphone"])
async def test_device_uses_actual_engine_touch_ua_and_rotation(tmp_path, device):
    if device == "iphone":
        async with async_playwright() as p:
            if not Path(p.webkit.executable_path).is_file():
                pytest.skip("WebKit not installed on this host; no iPhone emulation claimed")
    html = b"""<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><button id="tap">Tap</button><span id="value">0</span><input id="input"><div style="height:2500px"></div><script>window.touches=0;document.addEventListener('touchstart',()=>window.touches++);document.querySelector('#tap').onclick=()=>document.querySelector('#value').textContent='1';</script>"""
    config = device_browser(Browser(video=False), device)
    evidence = Evidence(tmp_path / "run")
    async with local_site(html) as url:
        runtime = BrowserRuntime(
            App(name="fixture", base_url=url, allowed_hosts=["127.0.0.1"], environment="local"),
            config,
            Task(id="device", goal="验证触屏环境"),
            evidence,
        )
        try:
            await runtime.start()
            observation = await runtime.observe(0)
            button = next(n for n in observation["nodes"] if n["dom_id"] == "tap")
            assert (await runtime.perform(Action(kind="click", target=button["id"], rationale="点击按钮")))[
                "ok"
            ]
            assert await runtime.page.locator("#value").inner_text() == "1"
            if device != "desktop":
                if device == "android":
                    assert await runtime.page.evaluate("navigator.maxTouchPoints") > 0
                assert await runtime.page.evaluate('matchMedia("(pointer:coarse)").matches') is True
                assert await runtime.page.evaluate("touches") > 0
                assert "Mobile" in runtime.environment["user_agent"]
                before = runtime.page.viewport_size
                assert (await runtime.perform(Action(kind="rotate", rationale="横屏继续")))["ok"]
                assert runtime.page.viewport_size == {"width": before["height"], "height": before["width"]}
                assert (await runtime.perform(Action(kind="scroll", pixels=400, rationale="滚动查看")))["ok"]
                await runtime.page.wait_for_timeout(150)
                assert await runtime.page.evaluate("scrollY") > 0
                assert runtime.environment["physical_device"] is False
                assert runtime.environment["limitations"]
            else:
                assert await runtime.page.evaluate("touches") == 0
        finally:
            await runtime.finish()


async def test_mobile_input_occlusion_is_detected(tmp_path):
    html = b"""<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><input id="name"><div id="cover" style="position:fixed;inset:0;background:#ddd;display:none"></div>"""
    async with local_site(html) as url:
        runtime = BrowserRuntime(
            App(name="x", base_url=url, allowed_hosts=["127.0.0.1"], environment="local"),
            Browser(device="android", video=False),
            Task(id="focus", goal="输入时检查遮挡"),
            Evidence(tmp_path / "run"),
        )
        try:
            await runtime.start()
            await runtime.page.locator("#name").focus()
            assertion = Assertion(id="focus", kind="focused_input_visible")
            assert await runtime.inspect_assertion(assertion)
            await runtime.page.locator("#cover").evaluate("e=>e.style.display='block'")
            assert await runtime.inspect_assertion(assertion) is False
            assert (
                await runtime.inspect_assertion(
                    Assertion(id="target", kind="element_unobscured", selector="#name")
                )
                is False
            )
        finally:
            await runtime.finish()


def test_device_validation_and_separate_task_identity():
    with pytest.raises(ValidationError):
        Browser(device="iphone", engine="chromium")
    for values in [[], ["desktop", "desktop"], ["unknown"]]:
        with pytest.raises(ValidationError):
            EvaluationOptions(devices=values)
    tasks = expand_tasks(
        [Task(id="nod", goal="点头测试")], Browser(video=False), ["desktop", "android", "iphone"]
    )
    assert [t.id for t in tasks] == ["nod-desktop", "nod-android", "nod-iphone"]
    assert [t.browser.engine for t in tasks] == ["chromium", "chromium", "webkit"]
    assert tasks[1].invariants and not tasks[0].invariants
    assert all(t.scenario_id == "nod" for t in tasks)
    assert "browser" not in tasks[1].public()


async def test_phone_detects_desktop_layout_without_viewport_meta(tmp_path):
    async with local_site(b"<button>Desktop-sized page</button>") as url:
        runtime = BrowserRuntime(
            App(name="x", base_url=url, allowed_hosts=["127.0.0.1"], environment="local"),
            Browser(device="android", video=False),
            Task(id="meta", goal="检查手机布局视口"),
            Evidence(tmp_path / "run"),
        )
        try:
            await runtime.start()
            assert await runtime.inspect_assertion(Assertion(id="viewport", kind="mobile_viewport")) is False
        finally:
            await runtime.finish()


async def test_android_press_to_talk_uses_touch_events(tmp_path):
    from ai_ux.schema import AudioFixture

    fixture = Path(__file__).resolve().parents[1] / "fixtures/audio/nod-loop.wav"
    audio = AudioFixture(path=str(fixture), transcript="点头", loop_ms=2000, start_ms=100, hold_ms=300)
    html = b"""<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><button id="talk">Talk</button><script>navigator.mediaDevices.getUserMedia({audio:true}).then(()=>window.ready=true);window.downType='';document.querySelector('#talk').onpointerdown=e=>window.downType=e.pointerType;document.querySelector('#talk').onpointerup=()=>window.released=true;</script>"""
    async with local_site(html) as url:
        runtime = BrowserRuntime(
            App(name="x", base_url=url, allowed_hosts=["127.0.0.1"], environment="local"),
            Browser(device="android", video=False),
            Task(id="talk", goal="按住说话", audio=audio),
            Evidence(tmp_path / "run"),
        )
        try:
            await runtime.start()
            await runtime.page.wait_for_function("window.ready===true")
            observation = await runtime.observe(0)
            node = next(n for n in observation["nodes"] if n["dom_id"] == "talk")
            result = await runtime.perform(
                Action(kind="speak", target=node["id"], rationale="触屏按住后松开")
            )
            assert result["ok"]
            assert await runtime.page.evaluate("window.downType") == "touch"
            assert await runtime.page.evaluate("window.released") is True
        finally:
            await runtime.finish()
