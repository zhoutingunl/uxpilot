import json
from contextlib import asynccontextmanager

from ai_ux.browser import BrowserRuntime
from ai_ux.evidence import Evidence
from ai_ux.media import review_intervals
from ai_ux.schema import Action, App, Browser, Task


@asynccontextmanager
async def local_site():
    import asyncio

    html = b"""<!doctype html><title>Fixture</title><button id="run">Count</button><span id="count">0</span>
    <div id="activation">DO-NOT-RECORD-THIS-CODE</div><input type="password" value="PASSWORD-SECRET">
    <a href="https://outside.invalid/">External</a>
    <script>document.querySelector('#run').onclick=()=>document.querySelector('#count').textContent='1';</script>"""

    async def handle(reader, writer):
        await reader.read(8192)
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: "
            + str(len(html)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + html
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


async def test_real_browser_observation_actions_masking_and_scope(tmp_path):
    async with local_site() as url:
        app = App(name="fixture", base_url=url, allowed_hosts=["127.0.0.1"], environment="local")
        e = Evidence(tmp_path / "run")
        runtime = BrowserRuntime(app, Browser(video=False), Task(id="test", goal="count"), e)
        try:
            await runtime.start()
            obs = await runtime.observe(0)
            assert "DO-NOT-RECORD" not in json.dumps(obs)
            assert "PASSWORD-SECRET" not in json.dumps(obs)
            button = next(n for n in obs["nodes"] if n["name"] == "Count")
            assert (await runtime.perform(Action(kind="click", target=button["id"], rationale="count")))["ok"]
            assert await runtime.page.locator("#count").inner_text() == "1"
            external = next(n for n in obs["nodes"] if n["name"] == "External")
            assert not (
                await runtime.perform(Action(kind="click", target=external["id"], rationale="test scope"))
            )["ok"]
            assert runtime.page.url == url
        finally:
            await runtime.finish()
        assert (e.root / "screenshots/step-000.png").is_file()


def test_review_intervals_keep_actions_and_explicitly_map_removed_waits():
    events = [
        {"kind": "agent_action_start", "t_ms": 40000},
        {"kind": "agent_action_end", "t_ms": 43000},
        {"kind": "product_action", "t_ms": 45000},
        {"kind": "voice_event", "t_ms": 48000},
    ]
    intervals = review_intervals(events, 100)
    assert intervals[0]["session_start_s"] == 0
    assert sum(p["session_end_s"] - p["session_start_s"] for p in intervals) < 30
    for t in [40, 43, 45, 48]:
        assert any(p["session_start_s"] <= t <= p["session_end_s"] for p in intervals)
    assert intervals[-1]["session_end_s"] == 100


async def test_video_chunks_cover_timeline_and_pad_short_final_clip(tmp_path):
    from ai_ux.media import command, split_review_video

    source = tmp_path / "source.mp4"
    await command(
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=640x360:r=2",
        "-t",
        "5.5",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(source),
    )
    clips = await split_review_video(source, tmp_path / "clips", 4)
    assert [(p["start_s"], p["end_s"]) for p in clips] == [(0, 4), (4, 5.5)]
    assert clips[-1]["padding_seconds"] == 2.5
    for clip in clips:
        raw = await command(
            "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(clip["path"])
        )
        assert float(json.loads(raw)["format"]["duration"]) == 4
