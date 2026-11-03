from __future__ import annotations

import json
import secrets
from pathlib import Path

from playwright.async_api import async_playwright
from pydantic import BaseModel

from .llm import LLM, read_key
from .media import command
from .schema import Config


class ProbeResult(BaseModel):
    first_color: str
    last_color: str
    first_code: str
    last_code: str


async def probe(config: Config, root: Path):
    root.mkdir(parents=True, exist_ok=False)
    first, last = secrets.token_hex(2).upper(), secrets.token_hex(2).upper()
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel=config.browser.channel, headless=True)
        page = await browser.new_page(viewport={"width": 640, "height": 360})
        for n, color, code in [(0, "red", first), (1, "blue", last)]:
            await page.set_content(
                f'<body style="margin:0;background:{color};display:grid;place-items:center;height:100vh;color:white;font:bold 70px sans-serif">{code}</body>'
            )
            await page.screenshot(path=str(root / f"{n}.png"))
        await browser.close()
    await command(
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        "0.5",
        "-i",
        str(root / "%d.png"),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "2",
        str(root / "probe.mp4"),
    )
    result = {
        "expected": {"first_color": "red", "last_color": "blue", "first_code": first, "last_code": last},
        "providers": [],
    }
    llm = LLM(config.models)
    try:
        for provider in [config.models.primary, config.models.fallback]:
            row = {
                "provider": provider.name,
                "model": provider.model,
                "credential_available": bool(read_key(provider)),
            }
            if row["credential_available"]:
                try:
                    value, call = await llm.structured(
                        "你是视频读取验证器。只描述输入视频实际内容。",
                        "按播放顺序给出开始和结束画面的背景颜色（英文小写）与各自中央四字符代码。",
                        ProbeResult,
                        video=root / "probe.mp4",
                        purpose="video_capability_probe",
                        providers=[provider],
                    )
                    row.update(
                        result=value.model_dump(),
                        call=call,
                        passed=value.first_color == "red"
                        and value.last_color == "blue"
                        and value.first_code == first
                        and value.last_code == last,
                    )
                except Exception as e:
                    row.update(passed=False, error=str(e))
            else:
                row.update(passed=False, error="credential_missing")
            result["providers"].append(row)
            (root / "probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        await llm.close()
    return result
