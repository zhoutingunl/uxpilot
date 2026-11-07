import base64
import json
import wave
from pathlib import Path

import httpx
import pytest
from test_browser_integration import local_site

from ai_ux.browser import BrowserRuntime
from ai_ux.evidence import Evidence
from ai_ux.schema import App, Assertion, Browser, Task, load_config
from ai_ux.verifier import check_events


def voice(kind, direction="received", **data):
    return {"kind": "voice_event", "direction": direction, "data": {"type": kind, **data}}


def playback(**overrides):
    return voice(
        "playback_status",
        "sent",
        **{
            "output_state": "running",
            "received_frames": 20,
            "scheduled_frames": 20,
            "ended_frames": 20,
            "dropped_frames": 0,
            "queued_ms": 0,
            "signal_peak_milli": 300,
            **overrides,
        },
    )


@pytest.mark.parametrize("language,label", [("zh-CN", "房间"), ("en", "Room"), ("fr", "Salle")])
async def test_room_startup_and_language_checks_do_not_depend_on_chinese_copy(tmp_path, language, label):
    html = f"""<!doctype html><html lang="{language}"><body>
      <strong id="room-label">{label} 7</strong>
      <div class="room-links"><a data-room="7" aria-current="page" href="?room=7">{label} 7</a></div>
      <script>history.replaceState(null,'','?room=7')</script></body></html>""".encode()
    async with local_site(html) as url:
        runtime = BrowserRuntime(
            App(name="Duck", base_url=url, allowed_hosts=["127.0.0.1"], adapter="duckking"),
            Browser(video=False),
            Task(id="language", goal="Inspect language"),
            Evidence(tmp_path / "run"),
        )
        try:
            await runtime.start()
            assert runtime.owned_room == "7" and runtime.policy.room == "7"
            assert await runtime.inspect_assertion(
                Assertion(id="language", kind="page_language", contains=language.split("-")[0])
            )
            assert not await runtime.inspect_assertion(
                Assertion(id="wrong-language", kind="page_language", contains="de")
            )
            assert (await runtime.observe(0))["language"] == language
        finally:
            # No real scene exists in this local fixture.
            runtime.owned_room = None
            await runtime.finish()


async def test_cleanup_preserves_isolated_room_cookie_and_origin_without_persisting_them(
    tmp_path, monkeypatch
):
    html = b"""<!doctype html><div class="room-links">
      <a data-room="7" aria-current="page">Room 7</a></div>
      <script>history.replaceState(null,'','?room=7')</script>"""
    async with local_site(html) as url:
        runtime = BrowserRuntime(
            App(name="Duck", base_url=url, allowed_hosts=["127.0.0.1"], adapter="duckking"),
            Browser(video=False),
            Task(id="cleanup", goal="Release room"),
            Evidence(tmp_path / "run"),
        )
        requests = []

        def handle(request):
            assert runtime.page.is_closed()
            assert request.headers["origin"] == url.rstrip("/")
            assert request.headers["cookie"] == "visitor=private-fixture-owner"
            assert request.url.params["room"] == "7"
            assert request.url.params["if_unused"] == "true"
            requests.append(request)
            return httpx.Response(204)

        original_client = httpx.AsyncClient
        monkeypatch.setattr(
            "ai_ux.browser.httpx.AsyncClient",
            lambda **kw: original_client(**kw, transport=httpx.MockTransport(handle)),
        )
        try:
            await runtime.start()
            await runtime.context.add_cookies(
                [{"name": "visitor", "value": "private-fixture-owner", "url": url}]
            )
        finally:
            await runtime.finish()
        assert len(requests) == 1 and runtime.cleanup == {"status": "released", "http_status": 204}
        assert "private-fixture-owner" not in (runtime.evidence.root / "events.jsonl").read_text()


def test_response_audio_excludes_input_invalid_frames_and_silence(tmp_path):
    runtime = BrowserRuntime(
        App(
            name="Duck", base_url="https://duck.qbox.net", allowed_hosts=["duck.qbox.net"], adapter="duckking"
        ),
        Browser(video=False),
        Task(id="voice", goal="Talk"),
        Evidence(tmp_path / "run"),
    )

    def frame(pcm=b"\x00\x01", rate=16000, direction="received"):
        runtime._frame(
            json.dumps({"type": "audio", "pcm": base64.b64encode(pcm).decode(), "sample_rate": rate}),
            direction,
        )

    frame(direction="sent")
    frame(pcm=b"")
    frame(pcm=b"\x01")
    frame(rate=None)
    assert runtime.response_audio_frames == 0
    frame(pcm=b"\x00\x00")
    assert not check_events(Assertion(id="audio", kind="audio_received"), runtime.evidence.events)
    frame()
    assert runtime.response_audio_frames == 2
    assert check_events(Assertion(id="audio", kind="audio_received"), runtime.evidence.events)
    runtime.audio_signal_seen = False  # Starting another input must permit a new first-audio marker.
    runtime.evidence.event("audio_input_start")
    assert not check_events(Assertion(id="audio", kind="audio_received"), runtime.evidence.events)
    frame()
    assert check_events(Assertion(id="audio", kind="audio_received"), runtime.evidence.events)


def test_voice_assertions_exclude_previous_turn_and_use_latest_connection_language():
    old = [
        voice("connect", "sent", language="en"),
        voice("transcript", role="user", text="Please nod"),
        {"kind": "first_audio"},
        voice("playback_end", aborted=False),
        playback(),
    ]
    events = old + [{"kind": "audio_input_start"}, voice("connect", "sent", language="fr")]
    assert not check_events(Assertion(id="stt", kind="voice_transcript", contains="nod"), events)
    assert not check_events(Assertion(id="audio", kind="audio_received"), events)
    assert not check_events(Assertion(id="end", kind="voice_turn_completed"), events)
    assert not check_events(Assertion(id="play", kind="audio_played"), events)
    assert not check_events(Assertion(id="lang", kind="voice_session_language", contains="en"), events)
    assert check_events(Assertion(id="lang", kind="voice_session_language", contains="fr"), events)
    events += [voice("playback_end", aborted=True)]
    assert not check_events(Assertion(id="end", kind="voice_turn_completed"), events)
    events += [voice("voice_milestone", phase="turn_end", result="completed")]
    assert check_events(Assertion(id="end", kind="voice_turn_completed"), events)


@pytest.mark.parametrize(
    "overrides,passed",
    [
        ({}, True),
        ({"output_state": "suspended"}, False),
        ({"ended_frames": 19}, False),
        ({"scheduled_frames": 19}, False),
        ({"queued_ms": 300}, False),
        ({"dropped_frames": 1}, False),
        ({"signal_peak_milli": 0}, False),
    ],
)
def test_playback_requires_non_silent_response_and_drained_browser_queue(overrides, passed):
    assertion = Assertion(id="play", kind="audio_played")
    events = [{"kind": "audio_input_start"}, {"kind": "first_audio"}, playback(**overrides)]
    assert check_events(assertion, events) is passed
    # An already completed greeting cannot pass the current turn.
    assert not check_events(assertion, [playback(), *events])
    assert not check_events(assertion, [e for e in events if e["kind"] != "first_audio"])


def test_multilingual_profile_audio_windows_include_all_synthetic_speech():
    config = load_config(Path("configs/duckking.yaml").resolve())
    tasks = {t.id: t for t in config.tasks}
    for lang in ("en", "fr"):
        task = tasks[f"cloud-voice-{lang}"]
        assert f"lang={lang}" in task.start_path
        assert {a.kind for a in task.assertions} >= {
            "page_language",
            "voice_session_language",
            "voice_transcript",
            "action_completed",
            "audio_received",
            "voice_turn_completed",
            "audio_played",
        }
        audio = task.audio
        assert audio.source == "minimax_tts" and audio.language == lang
        with wave.open(str(Path(f"fixtures/audio/nod-{lang}-loop.wav"))) as wav:
            assert wav.getnchannels() == 1 and wav.getsampwidth() == 2
            rate = wav.getframerate()
            assert wav.getnframes() * 1000 == rate * audio.loop_ms
            samples = memoryview(wav.readframes(wav.getnframes())).cast("h")
        audible = [i * 1000 / rate for i, value in enumerate(samples) if abs(value) > 50]
        assert audible and min(audible) >= audio.start_ms
        assert max(audible) < audio.start_ms + audio.hold_ms
