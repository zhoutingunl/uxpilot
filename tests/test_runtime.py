import base64
import json

import pytest
from test_browser_integration import local_site

from ai_ux.browser import BrowserRuntime
from ai_ux.evidence import Evidence
from ai_ux.schema import Action, App, Assertion, Browser, Task
from ai_ux.verifier import verify


async def test_generic_form_navigation_select_keyboard_and_health(tmp_path):
    async with local_site() as url:
        r = BrowserRuntime(
            App(name="Test", base_url=url, allowed_hosts=["127.0.0.1"], environment="local"),
            Browser(video=False),
            Task(id="test", goal="Use form"),
            Evidence(tmp_path / "run"),
        )
        try:
            await r.start()
            await r.page.set_content("""<title>Form</title><label for="name">Name</label><input id="name">
            <label for="choice">Choice</label><select id="choice"><option value="a">A</option><option value="b">B</option></select>
            <button id="submit">Confirm</button><div role="status" id="result"></div>
            <script>document.querySelector('#submit').onclick=()=>document.querySelector('#result').textContent='Saved '+document.querySelector('#name').value;</script>""")
            obs = await r.observe(0)

            def node(label):
                return next(n["id"] for n in obs["nodes"] if n["name"] == label)

            assert (await r.perform(Action(kind="fill", target=node("Name"), text="Test", rationale="name")))[
                "ok"
            ]
            assert (
                await r.perform(Action(kind="select", target=node("Choice"), text="b", rationale="select"))
            )["ok"]
            assert (
                await r.perform(Action(kind="press", target=node("Confirm"), key="Enter", rationale="submit"))
            )["ok"]
            for a in [
                Assertion(id="visible", kind="element_visible", selector="#result"),
                Assertion(id="text", kind="text", selector="#result", contains="Saved Test"),
                Assertion(id="url", kind="url_contains", contains="127.0.0.1"),
                Assertion(id="page", kind="page_loaded"),
                Assertion(id="errors", kind="no_page_errors"),
                Assertion(id="width", kind="no_horizontal_overflow"),
                Assertion(id="both", kind="viewport_overlap", selector="#name", other_selector="#choice"),
            ]:
                assert await r.inspect_assertion(a)
            assert (await r.perform(Action(kind="scroll", direction="down", rationale="scroll")))["ok"]
            assert (await r.perform(Action(kind="wait", seconds=0, rationale="wait")))["ok"]
            assert not (await r.perform(Action(kind="navigate", url=url + "unseen", rationale="unseen")))[
                "ok"
            ]
            assert not (await r.perform(Action(kind="click", target="invented", rationale="bad")))["ok"]
            assert (await r.perform(Action(kind="reload", rationale="refresh")))["ok"]
            with pytest.raises(ValueError):
                await r.inspect_assertion(Assertion(id="duck", kind="model_ready"))
            r._frame('{"type":"state","secret":"never-log-payload"}', "received", "/stream")
            assert r.evidence.events[-1]["kind"] == "websocket_frame"
            assert "never-log-payload" not in json.dumps(r.evidence.events[-1])
            check = await verify(r, [Assertion(id="bad", kind="text", selector="[broken", contains="x")])
            assert check["checks"][0]["status"] == "error"
        finally:
            await r.finish()
        assert r.cleanup["status"] == "not_applicable"


async def test_recorded_browser_video_and_evidence_are_real(tmp_path):
    async with local_site() as url:
        r = BrowserRuntime(
            App(name="Test", base_url=url, allowed_hosts=["127.0.0.1"], environment="local"),
            Browser(video=True, video_max_seconds=30),
            Task(id="test", goal="count"),
            Evidence(tmp_path / "run"),
        )
        try:
            await r.start()
            await r.observe(0)
            await r.page.wait_for_timeout(1500)
        finally:
            await r.finish()
        assert r.video_info["status"] == "ready"
        assert (r.evidence.root / "session.mp4").stat().st_size > 0
        assert (r.evidence.root / "review.mp4").stat().st_size > 0
        assert r.video_info["clock_uncertainty_ms"] >= 0


def test_duckking_protocol_keeps_only_relevant_events(tmp_path):
    r = BrowserRuntime(
        App(
            name="duck", base_url="https://duck.qbox.net", allowed_hosts=["duck.qbox.net"], adapter="duckking"
        ),
        Browser(video=False),
        Task(id="test", goal="voice"),
        Evidence(tmp_path / "run"),
    )
    for invalid in [b"audio", "not-json", "[]"]:
        r._frame(invalid, "received")
    r._frame(json.dumps({"type": "audio", "pcm": "bad!"}), "received")
    r._frame(
        json.dumps({"type": "audio", "pcm": base64.b64encode(b"\x00\x01").decode(), "sample_rate": 16000}),
        "received",
    )
    r._frame(
        json.dumps({"type": "transcript", "role": "user", "text": "hello", "session_id": "secret"}),
        "received",
    )
    assert r.response_audio_frames == 1 and r.response_audio_rate == 16000
    assert (r.evidence.root / "response.pcm").read_bytes() == b"\x00\x01"
    assert "session_id" not in json.dumps(r.evidence.events)
    assert r.evidence.events[-1]["data"]["text"] == "hello"
