import asyncio
import base64
import math
import struct
import wave
from contextlib import asynccontextmanager

from ai_ux.audio_recording import BrowserAudioRecorder
from ai_ux.browser import BrowserRuntime
from ai_ux.evidence import Evidence
from ai_ux.media import (
    command,
    encode_video,
    probe_media,
    relative_video_events,
    review_intervals,
    review_video,
)
from ai_ux.schema import Action, App, AudioFixture, Browser, Task


@asynccontextmanager
async def audio_site():
    html = """<meta charset="utf-8"><div id="room-label">房间 1 · 测试</div><div class="room-links"><a data-room="1" aria-current="page">房间 1</a></div>
    <button id="enable">Enable</button><button id="talk-button">Hold</button>
    <script>
    let ctx,stream;
    enable.onclick=async()=>{ctx=new AudioContext();await ctx.resume();stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:false,noiseSuppression:false,autoGainControl:false}});
      stream.getAudioTracks().forEach(t=>t.enabled=false);const source=ctx.createMediaStreamSource(stream),zero=ctx.createGain();zero.gain.value=0;source.connect(zero);zero.connect(ctx.destination);window.ready=true;};
    const talk=document.getElementById('talk-button');
    talk.onpointerdown=()=>stream.getAudioTracks().forEach(t=>t.enabled=true);
    talk.onpointerup=()=>{stream.getAudioTracks().forEach(t=>t.enabled=false);const tone=ctx.createOscillator(),gain=ctx.createGain();gain.gain.value=.2;tone.frequency.value=880;tone.connect(gain);gain.connect(ctx.destination);
      const at=ctx.currentTime+.2,stamp=ctx.getOutputTimestamp();window.expectedReply=performance.timeOrigin+stamp.performanceTime+(at-stamp.contextTime)*1000;
      tone.start(at);tone.stop(at+.5);};
    </script>""".encode()

    async def handle(reader, writer):
        request = await reader.read(8192)
        close = b"/api/scenes/close" in request.split(b"\r\n", 1)[0]
        body = b"" if close else html
        writer.write(
            (b"HTTP/1.1 204 No Content\r\n" if close else b"HTTP/1.1 200 OK\r\n")
            + f"Content-Length: {len(body)}\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/"
    finally:
        server.close()
        await server.wait_closed()


def tone_file(path, seconds=2, rate=48000, start=0, end=None, frequency=440):
    end = seconds if end is None else end
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        samples = [
            round(6000 * math.sin(2 * math.pi * frequency * i / rate)) if start <= i / rate < end else 0
            for i in range(round(seconds * rate))
        ]
        wav.writeframes(struct.pack("<" + "h" * len(samples), *samples))


def onset(path):
    with wave.open(str(path)) as wav:
        channels, rate = wav.getnchannels(), wav.getframerate()
        data = wav.readframes(wav.getnframes())
    values = struct.unpack("<" + "h" * (len(data) // 2), data)
    index = next(i for i, v in enumerate(values) if abs(v) > 1000)
    return index / channels / rate


def energy_at(path, frequency):
    start = onset(path) + 0.08
    with wave.open(str(path)) as wav:
        rate, channels = wav.getframerate(), wav.getnchannels()
        wav.setpos(round(start * rate))
        data = wav.readframes(2048)
    values = struct.unpack("<" + "h" * (len(data) // 2), data)[::channels]
    real = sum(value * math.cos(2 * math.pi * frequency * i / rate) for i, value in enumerate(values))
    imaginary = sum(value * math.sin(2 * math.pi * frequency * i / rate) for i, value in enumerate(values))
    return real * real + imaginary * imaginary


async def test_real_browser_records_actual_microphone_and_page_output_with_timing(tmp_path):
    fixture = tmp_path / "mic.wav"
    tone_file(fixture)
    task = Task(
        id="audio",
        goal="record audio",
        start_path="/?room=1",
        audio=AudioFixture(
            path=str(fixture), transcript="tone fixture", loop_ms=2000, start_ms=100, hold_ms=600
        ),
    )
    async with audio_site() as url:
        evidence = Evidence(tmp_path / "run")
        runtime = BrowserRuntime(
            App(
                name="fixture",
                base_url=url,
                allowed_hosts=["127.0.0.1"],
                environment="local",
                adapter="duckking",
            ),
            Browser(video=True, video_max_seconds=30),
            task,
            evidence,
        )
        try:
            await runtime.start()
            await runtime.page.locator("#enable").click()
            await runtime.page.wait_for_function("() => window.ready")
            observation = await runtime.observe(0)
            control = next(n for n in observation["nodes"] if n["dom_id"] == "talk-button")
            assert (await runtime.perform(Action(kind="speak", target=control["id"], rationale="test")))["ok"]
            expected = await runtime.page.evaluate("window.expectedReply")
            await runtime.page.wait_for_timeout(1000)
        finally:
            await runtime.finish()
        capture = runtime.video_info["audio_capture"]
        assert capture["status"] == "recorded", capture
        assert {s["kind"] for s in capture["streams"]} == {"input", "output"}
        output = next(s for s in capture["streams"] if s["kind"] == "output")
        input_stream = next(s for s in capture["streams"] if s["kind"] == "input")
        input_path, output_path = evidence.root / input_stream["path"], evidence.root / output["path"]
        assert energy_at(input_path, 440) > 10 * energy_at(input_path, 880)
        assert energy_at(output_path, 880) > 10 * energy_at(output_path, 440)
        input_start = next(e["t_ms"] for e in evidence.events if e["kind"] == "audio_input_start") / 1000
        input_end = next(e["t_ms"] for e in evidence.events if e["kind"] == "audio_input_end") / 1000
        assert input_start - 0.06 <= onset(input_path) <= input_end
        expected_seconds = (expected - runtime.audio_recorder.epoch_ms) / 1000
        assert abs(onset(evidence.root / output["path"]) - expected_seconds) < 0.07
        assert runtime.video_info["audio"] == "recorded_browser_audio"
        assert (await probe_media(evidence.root / "session.mp4"))["has_audio"]
        assert (await probe_media(evidence.root / "review.mp4"))["has_audio"]
        assert not (await probe_media(evidence.root / "judge.mp4"))["has_audio"]
        assert runtime.cleanup["status"] == "released"
        assert (evidence.root / "audio-capture.json").is_file()


async def test_audio_packet_limits_and_sparse_wave_headers(tmp_path):
    evidence = Evidence(tmp_path / "audio")
    recorder = BrowserAudioRecorder(evidence, max_seconds=3, max_bytes=1000000)

    class Page:
        main_frame = object()

    page = Page()
    recorder.page = page
    source = {"page": page, "frame": page.main_frame}
    packet = {
        "type": "pcm",
        "id": "../../outside",
        "kind": "output",
        "rate": 48000,
        "channels": 1,
        "epoch_ms": recorder.epoch_ms + 1000,
        "pcm": base64.b64encode(struct.pack("<10h", *[2000] * 10)).decode(),
    }
    recorder.receive({"page": Page(), "frame": page.main_frame}, packet)
    assert not recorder.streams
    recorder.receive(source, {**packet, "epoch_ms": recorder.epoch_ms + 9999999})
    assert not recorder.streams
    recorder.receive(source, packet)
    recorder.page = None
    await recorder.stop()
    await recorder.stop()
    info = recorder.metadata()
    assert info["warnings"] == ["invalid_audio_packet"]
    path = evidence.root / info["streams"][0]["path"]
    assert abs(onset(path) - 1) < 1 / 48000
    assert path.parent == evidence.root / "audio-capture"


async def test_review_keeps_audible_intervals_and_cuts_audio_and_video_together(tmp_path):
    source = tmp_path / "source.mp4"
    audio = tmp_path / "audio.wav"
    tone_file(audio, seconds=40, start=20, end=23, frequency=660)
    await command(
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=64x64:r=2",
        "-t",
        "40",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(source),
    )
    session = tmp_path / "session.mp4"
    await encode_video(source, session, 60, 3000000, audio_source=audio)
    events = relative_video_events(
        [{"kind": "recorded_audio", "t_ms": 35000, "start_ms": 21000, "end_ms": 24000}], 1000
    )
    assert events[0]["start_ms"] == 20000
    assert any(a["session_start_s"] <= 20 and a["session_end_s"] >= 23 for a in review_intervals(events, 40))
    review = await review_video(session, tmp_path / "review.mp4", events, 40, 3000000)
    assert review["audio"] == "preserved" and review["duration_seconds"] < 20
    decoded = tmp_path / "review.wav"
    await command(
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(tmp_path / "review.mp4"),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "48000",
        "-c:a",
        "pcm_s16le",
        str(decoded),
    )
    span = next(s for s in review["segments"] if s["session_start_s"] <= 20 < s["session_end_s"])
    assert abs(onset(decoded) - (span["review_start_s"] + 20 - span["session_start_s"])) < 0.06
    assert not (await probe_media(tmp_path / "judge.mp4"))["has_audio"]


async def test_disconnect_stops_recording_output_without_stopping_the_source(tmp_path):
    async with audio_site() as url:
        evidence = Evidence(tmp_path / "disconnect")
        runtime = BrowserRuntime(
            App(name="fixture", base_url=url, allowed_hosts=["127.0.0.1"], adapter="duckking"),
            Browser(video=True, video_max_seconds=20),
            Task(id="disconnect", goal="disconnect", start_path="/?room=1"),
            evidence,
        )
        try:
            await runtime.start()
            await runtime.page.evaluate(
                """async()=>{const c=new AudioContext();await c.resume();const tone=c.createOscillator(),gain=c.createGain();gain.gain.value=.2;tone.connect(gain);gain.connect(c.destination);tone.start();window.testAudio={c,tone,gain};}"""
            )
            await runtime.page.wait_for_timeout(350)
            await runtime.page.evaluate("testAudio.gain.disconnect(testAudio.c.destination)")
            await runtime.page.wait_for_timeout(500)
            await runtime.page.evaluate("testAudio.tone.stop()")
        finally:
            await runtime.finish()
        streams = runtime.video_info["audio_capture"]["streams"]
        assert len(streams) == 1 and streams[0]["kind"] == "output"
        intervals = streams[0]["intervals"]
        audible_span = (intervals[-1][1] - intervals[0][0]) / 1000
        assert 0.15 < audible_span < 0.55


def test_audio_capture_respects_input_authorization_and_byte_budget(tmp_path):
    evidence = Evidence(tmp_path / "limits")
    recorder = BrowserAudioRecorder(evidence, max_seconds=3, max_bytes=10)

    class Page:
        main_frame = object()

    page = Page()
    recorder.page = page
    source = {"page": page, "frame": page.main_frame}
    packet = {
        "type": "pcm",
        "id": "stream",
        "kind": "input",
        "rate": 48000,
        "channels": 1,
        "epoch_ms": recorder.epoch_ms,
        "pcm": base64.b64encode(b"\x10\x10" * 8).decode(),
    }
    recorder.receive(source, packet)
    assert not recorder.streams
    recorder.receive(source, {**packet, "kind": "output"})
    assert not recorder.streams and "audio_capture_byte_limit" in recorder.warnings


async def test_audio_stop_failure_still_closes_browser_and_releases_room(tmp_path):
    class BrokenRecorder:
        warnings = set()

        async def stop(self):
            raise OSError("simulated storage failure")

        def metadata(self):
            return {"status": "partial", "warnings": sorted(self.warnings)}

    async with audio_site() as url:
        runtime = BrowserRuntime(
            App(name="fixture", base_url=url, allowed_hosts=["127.0.0.1"], adapter="duckking"),
            Browser(video=False),
            Task(id="cleanup", goal="cleanup", start_path="/?room=1"),
            Evidence(tmp_path / "cleanup"),
        )
        await runtime.start()
        runtime.audio_recorder = BrokenRecorder()
        await runtime.finish()
        assert not runtime.browser.is_connected()
        assert runtime.cleanup["status"] == "released"
        assert "audio_capture_stop_failed" in runtime.audio_recorder.warnings


async def test_judge_never_receives_human_video_audio_even_without_review(tmp_path):
    from types import SimpleNamespace

    from ai_ux.agent import judge_run
    from ai_ux.schema import Judgement

    evidence = Evidence(tmp_path / "judge")
    human = evidence.root / "session.mp4"
    await command(
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=64x64:r=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=660:sample_rate=48000",
        "-t",
        "1",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        str(human),
    )
    provider = SimpleNamespace(name="fixture", video_chunk_seconds=None)

    class Judge:
        config = SimpleNamespace(primary=provider, fallback=provider)

        async def structured(self, system, prompt, schema, **kwargs):
            assert kwargs["video"].name == "judge.mp4"
            assert not (await probe_media(kwargs["video"]))["has_audio"]
            assert "silent" in prompt
            return Judgement(summary="Fixture", task_assessment="inconclusive"), {"provider": "fixture"}

    video = {"status": "ready", "audio": "recorded_browser_audio"}
    await judge_run(Judge(), evidence, Task(id="fixture", goal="fixture"), {}, video)
    assert (await probe_media(human))["has_audio"]
    video["review"] = {"path": "session.mp4", "model_path": "judge.mp4"}
    await judge_run(Judge(), evidence, Task(id="fixture", goal="fixture"), {}, video)
