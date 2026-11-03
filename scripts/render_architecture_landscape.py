"""Build the 16:9 architecture PNG/SVG and geometry for the editable PPTX exporter.

uv run python scripts/render_architecture_landscape.py
"""

from __future__ import annotations

import asyncio
import html
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "docs" / "diagrams"
WIDTH, HEIGHT = 1920, 1080
FONT = "Arial,'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif"
COLORS = {
    "paper": "F5F7FC",
    "card": "FFFFFF",
    "ink": "263449",
    "muted": "61718B",
    "blue": "2E73D7",
    "orange": "E18B24",
    "teal": "159CAC",
    "purple": "8056DA",
    "green": "25996D",
    "border": "DFE6F1",
}


class Layout:
    def __init__(self):
        self.elements = []

    def rect(self, x, y, w, h, fill="card", border="border", radius=18):
        self.elements.append(dict(kind="rect", x=x, y=y, w=w, h=h, fill=fill, border=border, radius=radius))

    def text(self, x, y, w, h, text, size=28, color="ink", bold=False, align="left", role="body"):
        self.elements.append(
            dict(
                kind="text",
                x=x,
                y=y,
                w=w,
                h=h,
                text=text,
                size=size,
                color=color,
                bold=bold,
                align=align,
                role=role,
            )
        )

    def line(self, x, y, x2, y2, color="border", arrow=False):
        self.elements.append(dict(kind="line", x=x, y=y, x2=x2, y2=y2, color=color, arrow=arrow))


def layout(data):
    out = Layout()
    out.text(72, 45, 1300, 88, data["title"], 72, bold=True, role="title")
    out.text(74, 134, 1450, 38, data["subtitle"], 28, color="muted", role="subtitle")
    out.text(1510, 70, 336, 40, data["version"], 26, color="muted", align="right")
    gap, box_w = 20, (1776 - 6 * 20) / 7
    for i, label in enumerate(data["flow"]):
        x = 72 + i * (box_w + gap)
        out.rect(
            x,
            198,
            box_w,
            68,
            fill="purple" if i == 4 else "card",
            border="purple" if i == 4 else "border",
            radius=12,
        )
        out.text(
            x + 10,
            211,
            box_w - 20,
            43,
            label,
            31,
            color="card" if i == 4 else "ink",
            bold=True,
            align="center",
        )
        if i < 6:
            out.line(x + box_w + 3, 232, x + box_w + gap - 3, 232, color="muted", arrow=True)
    for c, column in enumerate(data["columns"]):
        x = 72 + c * 604
        out.text(x, 292, 568, 42, column["label"], 30, color=column["color"], bold=True)
        for r, card in enumerate(column["cards"]):
            y = 347 + r * 194
            out.rect(x, y, 568, 176, radius=18)
            out.text(x + 24, y + 16, 520, 49, card["title"], 40, color=column["color"], bold=True)
            out.text(x + 24, y + 72, 520, 38, card["lines"][0], 28)
            out.text(x + 24, y + 110, 520, 38, card["lines"][1], 28)
            out.text(x + 24, y + 149, 520, 22, card["module"], 20, color="muted")
    out.rect(72, 940, 1776, 68, fill="ink", border="ink", radius=12)
    out.text(94, 953, 1725, 43, data["boundary"], 28, color="card", bold=True)
    out.text(74, 1025, 1300, 30, data["note"], 22, color="muted", role="footer")
    out.text(1380, 1025, 466, 30, data["footer"], 22, color="muted", align="right", role="footer")
    return {
        "width": WIDTH,
        "height": HEIGHT,
        "colors": COLORS,
        "elements": out.elements,
        "notes": data["notes"],
    }


def svg(scene):
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">',
        '<title id="title">UX Pilot 16:9 横版分层架构</title>',
        '<desc id="desc">顶部七步流程，下方三列九层架构，底部当前能力边界；适合横版幻灯片。</desc>',
        '<defs><marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0 0L5 3L0 6" fill="none" stroke="#61718B" stroke-width="1.1"/></marker></defs>',
        f'<rect width="100%" height="100%" fill="#{COLORS["paper"]}"/><g font-family="{html.escape(FONT, quote=True)}">',
    ]
    for e in scene["elements"]:
        if e["kind"] == "rect":
            parts.append(
                f'<rect x="{e["x"]:g}" y="{e["y"]:g}" width="{e["w"]:g}" height="{e["h"]:g}" rx="{e["radius"]}" fill="#{COLORS[e["fill"]]}" stroke="#{COLORS[e["border"]]}"/>'
            )
        elif e["kind"] == "line":
            extra = ' marker-end="url(#arrow)"' if e["arrow"] else ""
            parts.append(
                f'<path d="M{e["x"]:g} {e["y"]:g}L{e["x2"]:g} {e["y2"]:g}" fill="none" stroke="#{COLORS[e["color"]]}" stroke-width="2"{extra}/>'
            )
        else:
            anchor = {"left": "start", "center": "middle", "right": "end"}[e["align"]]
            x = e["x"] + ({"left": 0, "center": e["w"] / 2, "right": e["w"]}[e["align"]])
            parts.append(
                f'<text x="{x:g}" y="{e["y"] + e["size"]:g}" font-size="{e["size"]}" fill="#{COLORS[e["color"]]}" font-weight="{700 if e["bold"] else 400}" text-anchor="{anchor}" data-box="{e["x"]},{e["y"]},{e["w"]},{e["h"]}">{html.escape(e["text"])}</text>'
            )
    return "".join(parts) + "</g></svg>"


async def main():
    data = json.loads((DIRECTORY / "architecture-landscape.json").read_text())
    scene = layout(data)
    build = ROOT / "artifacts/diagram-build"
    build.mkdir(parents=True, exist_ok=True)
    (build / "architecture-layout.json").write_text(json.dumps(scene, ensure_ascii=False, indent=2) + "\n")
    path = DIRECTORY / "architecture.svg"
    path.write_text(svg(scene))
    async with async_playwright() as p:
        options = {"headless": True}
        if os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
            options["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]
        else:
            options["channel"] = "chrome"
        browser = await p.chromium.launch(**options)
        page = await browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=2)
        await page.goto(path.as_uri())
        await page.evaluate("document.fonts.ready")
        errors = await page.evaluate(
            """()=>[...document.querySelectorAll('text[data-box]')].filter(t=>{const [x,y,w,h]=t.dataset.box.split(',').map(Number),b=t.getBBox();return b.x<x-2||b.x+b.width>x+w+2||b.y<y-2||b.y+b.height>y+h+5}).map(t=>t.textContent)"""
        )
        if errors:
            raise ValueError(f"Text exceeds planned box: {errors}")
        await page.locator("svg").screenshot(path=str(DIRECTORY / "architecture.png"))
        await browser.close()
    print(
        json.dumps(
            {
                "png": "docs/diagrams/architecture.png",
                "svg": "docs/diagrams/architecture.svg",
                "ratio": "16:9",
                "pixels": [3840, 2160],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
