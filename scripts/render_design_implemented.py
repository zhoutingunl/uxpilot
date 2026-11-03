"""Render implemented-only design views; keep the original status images intact."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright
from render_architecture import FONT, MUTED, Drawing, esc, wrap

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "docs" / "diagrams"
WIDTH, HEIGHT = 1600, 900
MARGIN = 36
CONTENT = WIDTH - MARGIN * 2


def heading(d, title, subtitle, version):
    d.text(MARGIN, 59, title, size=34, weight=750)
    d.text(MARGIN, 97, subtitle, size=20, fill=MUTED)
    d.pill(MARGIN, 119, "已实现能力", color="#18764f", bg="#e6f4eb", size=17, solid=True)
    d.text(WIDTH - MARGIN, 139, version, size=17, fill=MUTED, anchor="end")


def svg(d, title):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">'
        f'<title id="title">{esc(title)}</title><desc id="desc">UX Pilot 当前已实现能力与实际技术，16:9 横版。</desc>'
        f'<rect width="100%" height="100%" fill="#f5f7fc"/><g font-family="{esc(FONT)}">'
        + "".join(d.parts)
        + "</g></svg>"
    )


def footer(d, message, note):
    d.rect(MARGIN, 780, CONTENT, 65, fill="#2b3b57", stroke="#2b3b57", radius=13)
    d.text(MARGIN + 22, 821, message, size=23, fill="white", weight=600)
    d.text(WIDTH - MARGIN, 880, note, size=16, fill=MUTED, anchor="end")


def roles(data):
    d = Drawing()
    heading(d, data["roles_title"], data["roles_subtitle"], data["version"])
    gap = 24
    width = (CONTENT - 2 * gap) / 3
    groups = [
        ("规划与执行", "#2e73d7", data["orchestration"] + [data["explorer"]]),
        ("Playwright + Browser Runtime", "#159cac", data["runtime"]),
        ("独立验收 + UX Judge", "#8056da", data["evaluation"]),
    ]
    for i, (label, color, cards) in enumerate(groups):
        x = MARGIN + i * (width + gap)
        d.text(x, 195, label, size=25, fill=color, weight=700)
        for j, card in enumerate(cards):
            y = 218 + j * 178
            d.rect(x, y, width, 160, radius=14)
            d.icon(x + 22, y + 24, card["icon"], color, size=23)
            d.text(x + 58, y + 45, card["title"], size=25, weight=700)
            lines = [line for text in card["lines"] for line in wrap(text, width - 44, 21)]
            for k, line in enumerate(lines):
                d.text(x + 22, y + 86 + k * 30, line, size=21, fill=MUTED)
        if i < 2:
            mid = 480
            d.parts.append(
                f'<path d="M{x + width + 4:g} {mid}h16m-5-5 5 5-5 5" fill="none" stroke="#aab6c8" stroke-width="2"/>'
            )
    footer(d, data["roles_footer"], data["footnote"])
    return svg(d, data["roles_title"])


def components(data):
    d = Drawing()
    heading(d, data["components_title"], data["components_subtitle"], data["version"])
    xs = [MARGIN + 20, 335, 865]
    d.rect(MARGIN, 173, CONTENT, 51, fill="#eaf0f8", radius=10)
    for x, label in zip(xs, ["组件", "实际采用技术", "已实现用途"]):
        d.text(x, 207, label, size=24, weight=700)
    for i, row in enumerate(data["components"]):
        y = 234 + i * 105
        d.rect(MARGIN, y, CONTENT, 94, fill="white" if i % 2 == 0 else "#fbfcff", radius=10)
        d.text(xs[0], y + 39, row["name"], size=23, weight=650)
        for j, line in enumerate(wrap(row["technology"], xs[2] - xs[1] - 28, 22)):
            d.text(xs[1], y + 38 + j * 29, line, size=22)
        for j, line in enumerate(row["lines"]):
            d.text(xs[2], y + 34 + j * 30, line, size=20, fill=MUTED)
    footer(d, data["components_footer"], data["footnote"])
    return svg(d, data["components_title"])


async def main():
    data = json.loads((DIRECTORY / "design-implemented.json").read_text())
    outputs = {"design-roles-implemented": roles(data), "design-components-implemented": components(data)}
    async with async_playwright() as p:
        options = {"headless": True}
        if os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
            options["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]
        else:
            options["channel"] = "chrome"
        browser = await p.chromium.launch(**options)
        page = await browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=2)
        for name, content in outputs.items():
            path = DIRECTORY / f"{name}.svg"
            path.write_text(content)
            await page.goto(path.as_uri())
            await page.evaluate("document.fonts.ready")
            overflow = await page.evaluate(
                """() => {const s=document.querySelector('svg');return [...s.querySelectorAll('text')].filter(t=>{const b=t.getBBox();return b.x<0||b.x+b.width>s.viewBox.baseVal.width||b.y+b.height>s.viewBox.baseVal.height}).map(t=>t.textContent)}"""
            )
            if overflow:
                raise ValueError(f"Diagram overflow: {overflow}")
            await page.locator("svg").screenshot(path=str(DIRECTORY / f"{name}.png"))
            print(str(path.relative_to(ROOT)))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
