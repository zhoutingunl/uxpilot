import base64
import io
import json
import math
import struct
import wave
from types import SimpleNamespace

import httpx
import pytest

from ai_ux.browser import BrowserRuntime
from ai_ux.evidence import Evidence
from ai_ux.schema import App, Assertion, AudioFixture, Browser, Speech, Task
from ai_ux.speech import SpeechClient, SpeechError
from ai_ux.verifier import check_events


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"".join(struct.pack("<h", int(math.sin(i / 8) * 2000)) for i in range(6400)))
    return output.getvalue()


def settings(monkeypatch):
    monkeypatch.setenv("TEST_SPEECH_KEY", "speech-private-key")
    return Speech(key_env="TEST_SPEECH_KEY", key_file=None, base_url="https://speech.test")


def task():
    return Task(
        id="voice",
        goal="用法语点头",
        response_language="fr",
        audio=AudioFixture(source="minimax_tts", transcript="Fais oui de la tête.", language="fr"),
    )


@pytest.mark.parametrize("language,boost", [("zh", "Chinese"), ("en", "English"), ("fr", "French")])
async def test_tts_generates_complete_microphone_cycle_without_mutating_task(
    tmp_path, monkeypatch, language, boost
):
    evidence = Evidence(tmp_path / "run")
    original = task()
    original.audio.language = language

    def handle(request):
        payload = json.loads(request.content)
        assert payload["model"] == "speech-2.8-hd" and payload["language_boost"] == boost
        assert payload["voice_setting"]["voice_id"] == "male-qn-qingse"
        assert request.headers["authorization"] == "Bearer speech-private-key"
        return httpx.Response(
            200, json={"base_resp": {"status_code": 0}, "data": {"audio": wav_bytes().hex()}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        speech = SpeechClient(settings(monkeypatch), evidence, client)
        prepared = await speech.prepare(original)
        assert prepared is not original and original.audio.path is None
        with wave.open(prepared.audio.path) as wav:
            assert wav.getnframes() == 12 * 48000 and wav.getnchannels() == 1
            samples = memoryview(wav.readframes(wav.getnframes())).cast("h")
        audible = [i * 1000 / 48000 for i, v in enumerate(samples) if abs(v) > 50]
        assert (
            min(audible) >= original.audio.start_ms
            and max(audible) < original.audio.start_ms + original.audio.hold_ms
        )
        assert len(speech.calls) == 1
        await speech.close()
    assert "speech-private-key" not in (evidence.root / "speech-calls.json").read_text()


@pytest.mark.parametrize(
    "response,error",
    [
        (httpx.Response(401, text="speech-private-key"), "speech_http_401"),
        (
            httpx.Response(
                200, json={"base_resp": {"status_code": 2049, "status_msg": "speech-private-key"}}
            ),
            "speech_provider_2049",
        ),
        (httpx.Response(200, json={"data": {"audio": "not-hex"}}), "tts_invalid_audio"),
        (
            httpx.Response(200, json={"data": {"audio": b"not wav".hex()}}),
            "tts_audio_does_not_fit_hold_window",
        ),
        (httpx.Response(200, json=[]), "speech_AttributeError"),
    ],
)
async def test_provider_failures_are_explicit_and_do_not_leak_bodies(tmp_path, monkeypatch, response, error):
    evidence = Evidence(tmp_path / "run")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
        speech = SpeechClient(settings(monkeypatch), evidence, client)
        with pytest.raises(SpeechError, match=error):
            await speech.prepare(task())
    assert "speech-private-key" not in json.dumps(speech.calls)


def completed_reply(evidence):
    evidence.event("audio_input_start")
    evidence.event("first_audio")
    evidence.event("voice_event", direction="received", data={"type": "playback_end", "aborted": False})
    evidence.event(
        "voice_event",
        direction="sent",
        data={
            "type": "playback_status",
            "output_state": "running",
            "received_frames": 1,
            "scheduled_frames": 1,
            "ended_frames": 1,
            "dropped_frames": 0,
            "queued_ms": 0,
            "signal_peak_milli": 100,
        },
    )


async def test_independent_asr_sees_audio_without_reference_text_and_drives_verification(
    tmp_path, monkeypatch
):
    evidence = Evidence(tmp_path / "run")
    completed_reply(evidence)
    clip = evidence.root / "response.wav"
    clip.write_bytes(wav_bytes())

    def handle(request):
        assert request.url.path == "/v1/speech_to_text" and request.headers["language"] == "fr"
        assert b"asr-1.0" in request.content and b"RIFF" in request.content
        assert b"Fais oui" not in request.content
        return httpx.Response(200, json={"text": "J'ai hoché la TÊTE !"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        speech = SpeechClient(settings(monkeypatch), evidence, client)
        result = await speech.review_response(
            SimpleNamespace(response_audio_bytes=100, export_response_audio=lambda: clip), task()
        )
        assert result["status"] == "completed"
        assertion = Assertion(id="spoken", kind="response_speech", contains="tête")
        assert check_events(assertion, evidence.events)
        evidence.event("audio_input_start")
        assert not check_events(assertion, evidence.events)


@pytest.mark.parametrize(
    "case,status",
    [
        ("missing", "not_received"),
        ("partial", "incomplete_playback"),
        ("invalid", "invalid_or_incomplete_audio"),
        ("empty", "error"),
    ],
)
async def test_missing_partial_and_invalid_response_never_pass_asr(tmp_path, monkeypatch, case, status):
    evidence = Evidence(tmp_path / "run")
    if case in {"invalid", "empty"}:
        completed_reply(evidence)
    clip = evidence.root / "response.wav"
    clip.write_bytes(wav_bytes())

    async def wait(_):
        pass

    runtime = SimpleNamespace(
        response_audio_bytes=0 if case == "missing" else 100,
        export_response_audio=lambda: None if case == "invalid" else clip,
        page=SimpleNamespace(wait_for_timeout=wait),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"text": ""}))
    ) as client:
        speech = SpeechClient(settings(monkeypatch), evidence, client)
        assert (await speech.review_response(runtime, task()))["status"] == status
    assert not check_events(Assertion(id="spoken", kind="response_speech", contains="tête"), evidence.events)


def test_export_response_contains_only_latest_input_and_rejects_mixed_or_truncated_pcm(tmp_path):
    runtime = BrowserRuntime(
        App(
            name="Duck", base_url="https://duck.qbox.net", allowed_hosts=["duck.qbox.net"], adapter="duckking"
        ),
        Browser(video=False),
        task(),
        Evidence(tmp_path / "run"),
    )
    assert runtime.export_response_audio() is None

    def frame(pcm, rate=16000):
        runtime._frame(
            json.dumps({"type": "audio", "pcm": base64.b64encode(pcm).decode(), "sample_rate": rate}),
            "received",
        )

    frame(b"\x01\x00")
    runtime.response_turn_start = runtime.response_audio_bytes
    runtime.response_turn_rates.clear()
    frame(b"\x02\x00\x03\x00")
    with wave.open(str(runtime.export_response_audio())) as wav:
        assert wav.readframes(2) == b"\x02\x00\x03\x00"
    frame(b"\x04\x00", 24000)
    assert runtime.export_response_audio() is None
    runtime.response_turn_rates = {16000}
    runtime.response_audio_bytes += 2
    assert runtime.export_response_audio() is None
