"""Render the layered architecture source as portable SVG, PNG and a local HTML viewer.

Run with: uv run python scripts/render_architecture.py
Only documentation files are read/written; no LLM, network or application calls.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import unicodedata
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "diagrams"
WIDTH = 1600
MARGIN = 32
CONTENT = WIDTH - 2 * MARGIN
FONT = "Inter,Arial,'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif"
INK = "#263449"
MUTED = "#61718b"
BORDER = "#dfe6f1"
ICONS = {
    "folder": '<path d="M3 6h6l2 3h10v11H3z"/><path d="M3 6V4h7l2 2h7v3"/>',
    "sparkles": '<path d="m12 2 2.5 7.5L22 12l-7.5 2.5L12 22l-2.5-7.5L2 12l7.5-2.5z"/><path d="m20 2 .5 2 2 .5-2 .5-.5 2-.5-2-2-.5 2-.5z"/>',
    "cursor": '<path d="m4 2 15 11-7 1-3 7z"/><path d="m12 14 5 7"/>',
    "check": '<rect x="3" y="3" width="18" height="18" rx="4"/><path d="m7 12 3 3 7-7"/>',
    "video": '<rect x="2" y="5" width="14" height="14" rx="3"/><path d="m16 10 6-4v12l-6-4"/>',
    "code": '<path d="m8 6-6 6 6 6m8-12 6 6-6 6M14 3l-4 18"/>',
    "document": '<path d="M5 2h10l4 4v16H5zM15 2v5h4M8 11h8M8 15h8M8 19h5"/>',
    "archive": '<path d="M4 8h16v13H4zM2 3h20v5H2zM9 12h6"/>',
    "repeat": '<path d="M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5m-4 8a8 8 0 0 0 14 3l3-3m0 5v-5h-5"/>',
    "eye": '<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/>',
    "layers": '<path d="m2 7 10-5 10 5-10 5zM2 12l10 5 10-5M2 17l10 5 10-5"/>',
    "terminal": '<rect x="2" y="3" width="20" height="18" rx="3"/><path d="m6 8 4 4-4 4m7 0h5"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><ellipse cx="12" cy="12" rx="4" ry="10"/><path d="M2 12h20"/>',
    "sliders": '<path d="M5 2v6m0 4v10M12 2v12m0 4v4M19 2v3m0 4v13"/><circle cx="5" cy="10" r="2"/><circle cx="12" cy="16" r="2"/><circle cx="19" cy="7" r="2"/>',
    "timer": '<circle cx="12" cy="13" r="9"/><path d="M9 1h6M12 4V1m0 7v6l4 2"/>',
    "scissors": '<circle cx="5" cy="5" r="3"/><circle cx="5" cy="19" r="3"/><path d="m7 7 15 14M7 17 22 3"/>',
    "shield": '<path d="m12 2 9 4v6c0 6-9 10-9 10S3 18 3 12V6z"/><path d="m8 12 3 3 5-6"/>',
    "cloud": '<path d="M7 19a5 5 0 0 1-1-10 7 7 0 0 1 13-1 5.5 5.5 0 0 1 0 11z"/>',
    "lock": '<rect x="4" y="10" width="16" height="12" rx="3"/><path d="M7 10V7a5 5 0 0 1 10 0v3m-5 4v4"/>',
    "chart": '<path d="M3 2v19h19M7 17v-5m5 5V6m5 11V9"/>',
}


def esc(value):
    return html.escape(str(value), quote=True)


def text_width(value, size):
    units = 0.0
    for char in value:
        if unicodedata.east_asian_width(char) in {"W", "F"}:
            units += 1.0
        elif char in "ilI.,:; !'|":
            units += 0.3
        elif char in "MW@":
            units += 0.9
        else:
            units += 0.59
    return units * size


def wrap(value, width, size):
    lines = []
    current = ""
    for char in value:
        if current and text_width(current + char, size) > width:
            # Prefer whitespace boundaries for English, without splitting long CJK sentences badly.
            last = current.rfind(" ")
            if last > len(current) * 0.55:
                lines.append(current[:last])
                current = current[last + 1 :] + char
            else:
                lines.append(current)
                current = char
        else:
            current += char
    if current:
        lines.append(current)
    return lines


class Drawing:
    def __init__(self):
        self.parts = []

    def rect(self, x, y, w, h, fill="white", stroke=BORDER, radius=12):
        self.parts.append(
            f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="{radius}" fill="{fill}" stroke="{stroke}"/>'
        )

    def text(self, x, y, content, size=17, fill=INK, weight=400, anchor="start"):
        self.parts.append(
            f'<text x="{x:g}" y="{y:g}" font-size="{size:g}" fill="{fill}" font-weight="{weight}" text-anchor="{anchor}">{esc(content)}</text>'
        )

    def icon(self, x, y, name, color, size=21):
        self.parts.append(
            f'<g transform="translate({x:g},{y:g}) scale({size / 24:g})" fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</g>'
        )

    def pill(self, x, y, content, *, color=MUTED, bg="white", size=15, solid=False):
        w = text_width(content, size) + 26
        self.rect(x, y, w, 30, fill=bg, stroke=bg if solid else BORDER, radius=15)
        self.text(x + 13, y + 20, content, size=size, fill=color, weight=600)
        return w

    def flow(self, data, y):
        color = data["color"]
        self.rect(MARGIN, y - 17, 4, 20, fill=color, stroke=color, radius=2)
        self.text(MARGIN + 14, y, data["title"], size=20, fill=MUTED, weight=650)
        y += 19
        n = len(data["steps"])
        gap = 30
        w = (CONTENT - gap * (n - 1)) / n
        inner = w - 30
        cards = []
        for card in data["steps"]:
            lines = [line for paragraph in card["lines"] for line in wrap(paragraph, inner, 16)]
            cards.append((card, lines))
        h = 68 + max(len(lines) for _, lines in cards) * 24 + 15
        for i, (card, lines) in enumerate(cards):
            x = MARGIN + i * (w + gap)
            tint = "#f5f1ff" if card.get("tone") == "ai" else "#ffffff"
            accent = "#8864de" if card.get("tone") == "ai" else color
            self.rect(x, y, w, h, fill=tint, radius=10)
            self.parts.append(
                f'<path d="M{x + 11:g},{y:g}H{x + w - 11:g}" stroke="{accent}" stroke-width="3" stroke-linecap="round"/>'
            )
            self.icon(x + 15, y + 15, card["icon"], accent)
            self.text(x + 15, y + 61, card["title"], size=19, weight=650)
            for j, line in enumerate(lines):
                self.text(x + 15, y + 88 + j * 24, line, size=16, fill=MUTED)
            if i < n - 1:
                mid = y + h / 2
                self.parts.append(
                    f'<path d="M{x + w + 6:g} {mid:g}h18m-5-5 5 5-5 5" fill="none" stroke="#c1ccdf" stroke-width="2" stroke-linecap="round"/>'
                )
        return y + h + 35

    def layer(self, data, y):
        gap = 13
        n = len(data["cards"])
        w = (CONTENT - 38 - gap * (n - 1)) / n
        cards = []
        for card in data["cards"]:
            lines = [line for paragraph in card["body"] for line in wrap(paragraph, w - 30, 16)]
            modules = wrap(card["module"], w - 30, 13.5)
            cards.append((card, lines, modules))
        card_h = 60 + max(len(lines) * 24 + len(module) * 19 for _, lines, module in cards) + 18
        h = card_h + 65
        self.rect(MARGIN, y, CONTENT, h, radius=16)
        self.rect(MARGIN, y + 13, 5, h - 26, fill=data["color"], stroke=data["color"], radius=2)
        self.text(MARGIN + 20, y + 32, data["id"], size=19, fill=data["color"], weight=750)
        self.text(MARGIN + 58, y + 32, data["title"], size=21, weight=700)
        pill_w = text_width(data["badge"], 14) + 26
        self.pill(
            WIDTH - MARGIN - pill_w - 18,
            y + 12,
            data["badge"],
            color="white",
            bg=data["color"],
            size=14,
            solid=True,
        )
        card_y = y + 48
        for i, (card, lines, modules) in enumerate(cards):
            x = MARGIN + 19 + i * (w + gap)
            tint = {"ai": "#f7f4ff", "adapter": "#fff7ef"}.get(card.get("tone"), "#f9fbfe")
            self.rect(x, card_y, w, card_h, fill=tint, stroke="#e4eaf3", radius=9)
            self.icon(x + 15, card_y + 15, card["icon"], data["color"], 19)
            self.text(x + 42, card_y + 31, card["title"], size=18, weight=650)
            for j, line in enumerate(lines):
                self.text(x + 15, card_y + 59 + j * 24, line, size=16, fill=MUTED)
            module_y = card_y + card_h - 14 - (len(modules) - 1) * 19
            for j, line in enumerate(modules):
                self.text(x + 15, module_y + j * 19, line, size=13.5, fill="#75849a", weight=500)
        return y + h + 18


def build_svg(source):
    d = Drawing()
    d.text(MARGIN, 54, source["title"], size=32, weight=750)
    d.text(MARGIN, 86, source["subtitle"], size=18, fill=MUTED, weight=500)
    d.pill(WIDTH - MARGIN - 170, 31, source["version"], color="#3f628c", bg="#eaf0f8", size=15, solid=True)
    x = MARGIN
    for label, content in source["chips"]:
        x += d.pill(x, 106, f"{label} · {content}", size=15) + 12
    x = MARGIN
    for content in source["principles"]:
        x += d.pill(x, 148, content, size=15) + 12
    y = 215
    for flow in source["flows"]:
        y = d.flow(flow, y)
    d.rect(MARGIN, y - 17, 4, 20, fill="#3275d6", stroke="#3275d6", radius=2)
    d.text(MARGIN + 14, y, "分层架构", size=20, fill=MUTED, weight=650)
    d.text(WIDTH - MARGIN, y, "每层展示职责、关键机制与实现模块", size=16, fill="#8794aa", anchor="end")
    y += 20
    for layer in source["layers"]:
        y = d.layer(layer, y)
    footer = source["footer"]
    d.rect(MARGIN, y, CONTENT, 148, fill="#293b5a", stroke="#293b5a", radius=14)
    d.text(MARGIN + 23, y + 31, footer["title"], size=21, fill="#ffffff", weight=650)
    for i, item in enumerate(footer["items"]):
        x = MARGIN + 23 + (i % 2) * (CONTENT / 2)
        yy = y + 66 + (i // 2) * 35
        d.rect(x, yy - 10, 5, 5, fill="#f1bd65", stroke="#f1bd65", radius=2)
        d.text(x + 15, yy, item, size=16, fill="#e3e9f3")
    d.text(MARGIN, y + 176, footer["note"], size=15, fill=MUTED)
    d.text(
        WIDTH - MARGIN,
        y + 202,
        "UX Pilot · github.com/zhoutingunl/uxpilot · v0.2",
        size=14,
        fill="#8b98ae",
        anchor="end",
    )
    height = y + 230
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height:g}" viewBox="0 0 {WIDTH} {height:g}" role="img" aria-labelledby="title desc">'
        f'<title id="title">{esc(source["title"])}</title>'
        '<desc id="desc">顶部为主流程和恢复复测流程，下方按接入、规划编排、浏览器、证据媒体、验证评审、AI 能力、源码定位、报告复核、策略工程质量九层展示当前实现。</desc>'
        f'<rect width="100%" height="100%" fill="#f5f7fc"/><g font-family="{esc(FONT)}">'
        + "".join(d.parts)
        + "</g></svg>"
    )


def viewer():
    return """<!doctype html><html lang="zh-CN"><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>UX Pilot 分层架构图</title><style>
body{margin:0;background:#e8edf5;font:15px/1.6 system-ui;color:#263449}header{position:sticky;top:0;z-index:2;display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:12px 22px;background:#fff;border-bottom:1px solid #d8e0ed}strong{margin-right:auto}button,a{font:inherit;border:1px solid #d8e0ed;border-radius:7px;padding:5px 12px;background:#fff;color:#315a8b;text-decoration:none;cursor:pointer}button[aria-pressed=true]{background:#eaf1fb;border-color:#548bd1}main{padding:24px;overflow:auto}img{display:block;width:min(100%,1600px);height:auto;margin:0 auto;box-shadow:0 8px 32px #20345414}.actual img{width:1600px;max-width:none}
</style><header><strong>UX Pilot · 分层架构图</strong><button id="fit" aria-pressed="true">适应窗口</button><button id="actual" aria-pressed="false">原始尺寸</button><a href="architecture.svg" download>下载 SVG</a><a href="architecture.png" download>下载 PNG</a></header>
<main id="view"><img src="architecture.svg" alt="UX Pilot 主流程、恢复复测与九层系统架构"></main><script>
for(const mode of ['fit','actual'])document.getElementById(mode).onclick=()=>{document.getElementById('view').classList.toggle('actual',mode==='actual');for(const id of ['fit','actual'])document.getElementById(id).setAttribute('aria-pressed',String(id===mode))};
</script></html>"""


async def render(source_path: Path, svg_only=False):
    source = json.loads(source_path.read_text())
    directory = source_path.parent
    svg = directory / "architecture.svg"
    svg.write_text(build_svg(source))
    (directory / "architecture.html").write_text(viewer())
    if svg_only:
        return
    async with async_playwright() as p:
        options = {"headless": True}
        if os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
            options["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]
        else:
            options["channel"] = "chrome"
        browser = await p.chromium.launch(**options)
        page = await browser.new_page(viewport={"width": WIDTH, "height": 1200}, device_scale_factor=1.5)
        await page.goto(svg.as_uri())
        await page.evaluate("document.fonts.ready")
        outside = await page.evaluate(
            """() => {const s=document.querySelector('svg');return [...s.querySelectorAll('text')].filter(t=>{const b=t.getBBox();return b.x<0||b.x+b.width>s.viewBox.baseVal.width||b.y+b.height>s.viewBox.baseVal.height}).map(t=>t.textContent)}"""
        )
        if outside:
            raise ValueError(f"Text outside diagram: {outside}")
        await page.locator("svg").screenshot(path=str(directory / "architecture.png"))
        await browser.close()
    print(
        json.dumps(
            {
                "svg": str(svg.relative_to(ROOT)),
                "png": str((directory / "architecture.png").relative_to(ROOT)),
                "layers": len(source["layers"]),
            },
            ensure_ascii=False,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DOCS / "architecture.json")
    parser.add_argument("--svg-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(render(args.source.resolve(), args.svg_only))


if __name__ == "__main__":
    main()
