"""Recreate the two proposed designs with implementation status labels.

Run: uv run python scripts/render_design_status.py
Documentation only: no LLM, network requests or application changes.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright
from render_architecture import FONT, MUTED, Drawing, esc, wrap

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "docs" / "diagrams"
WIDTH = 1600
MARGIN = 36
CONTENT = WIDTH - MARGIN * 2
STATUSES = {
    "implemented": ("已实现", "#18764f", "#e6f4eb"),
    "partial": ("部分实现", "#94620c", "#fff3d8"),
    "missing": ("未实现", "#b8433e", "#fff0ef"),
    "not_adopted": ("未采用", "#68738a", "#edf0f5"),
    "alternative": ("替代实现", "#265b9c", "#e7f0fc"),
}


def status(d, x, y, value, label=None, size=17):
    text, color, background = STATUSES[value]
    return d.pill(x, y, label or text, color=color, bg=background, size=size, solid=True)


def legend(d, y):
    x = MARGIN
    for key in ["implemented", "partial", "missing", "not_adopted"]:
        x += status(d, x, y, key, size=16) + 14
    d.text(x + 8, y + 21, "依据代码状态标注；未采用 ≠ 能力缺失", size=16, fill=MUTED)


def heading(d, title, subtitle, version):
    d.text(MARGIN, 58, title, size=32, weight=750)
    d.text(MARGIN, 94, subtitle, size=18, fill=MUTED)
    d.text(WIDTH - MARGIN, 132, version, size=16, fill=MUTED, anchor="end")
    legend(d, 115)


def arrow(d, y):
    x = WIDTH / 2
    d.parts.append(
        f'<path d="M{x} {y}v28m-7-7 7 7 7-7" fill="none" stroke="#aab6c8" stroke-width="2.5" stroke-linecap="round"/>'
    )


def section_label(d, title, y, subtitle=""):
    d.text(MARGIN + 22, y, title, size=24, weight=700)
    if subtitle:
        d.text(WIDTH - MARGIN - 22, y, subtitle, size=16, fill=MUTED, anchor="end")


def blocks(d, items, y, height, cols):
    gap = 16
    w = (CONTENT - 44 - gap * (cols - 1)) / cols
    for i, item in enumerate(items):
        x = MARGIN + 22 + (i % cols) * (w + gap)
        yy = y + (i // cols) * (height + gap)
        _, color, background = STATUSES[item["status"]]
        d.rect(x, yy, w, height, fill="#fbfcfe", radius=10)
        d.rect(x, yy + 12, 3, height - 24, fill=color, stroke=color, radius=1)
        d.text(x + 16, yy + 31, item["title"], size=20, weight=650)
        status(d, x + 16, yy + 45, item["status"], size=16)
        if item.get("subtitle"):
            d.text(x + 16, yy + 105, item["subtitle"], size=18, fill=MUTED, weight=600)
            start = yy + 138
            lines = item["lines"]
        else:
            start = yy + 101
            lines = [item["detail"]]
        wrapped = [line for text in lines for line in wrap(text, w - 32, 17)]
        for n, line in enumerate(wrapped):
            d.text(x + 16, start + n * 25, line, size=17, fill=MUTED)


def svg(d, title, height):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" role="img" aria-labelledby="title desc">'
        f'<title id="title">{esc(title)}</title>'
        '<desc id="desc">按照现有代码标注已实现、部分实现、未实现和未采用，详情与依据见 design-implementation-status.md。</desc>'
        f'<rect width="100%" height="100%" fill="#f5f7fc"/><g font-family="{esc(FONT)}">'
        + "".join(d.parts)
        + "</g></svg>"
    )


def role_diagram(data):
    d = Drawing()
    heading(d, data["title"], data["subtitle"], data["version"])
    d.rect(MARGIN, 177, CONTENT, 255, radius=16)
    section_label(d, "任务规划、角色调度、执行控制", 216, "原设计的编排层")
    blocks(d, data["orchestration"], 239, 170, 3)
    arrow(d, 447)
    d.rect(MARGIN, 491, CONTENT, 273, radius=16)
    section_label(d, "体验角色", 532, "当前只有一个 Explorer + 可配置 persona")
    blocks(d, data["roles"], 555, 187, 3)
    arrow(d, 779)
    d.rect(MARGIN, 822, CONTENT, 227, radius=16)
    section_label(d, "Playwright + Browser Runtime", 863, "操作与证据分项标注")
    blocks(d, data["runtime"], 883, 143, 4)
    arrow(d, 1064)
    d.rect(MARGIN, 1107, CONTENT, 227, radius=16)
    section_label(d, "UX Judge Agent + 报告聚合", 1148, "Judge 评审；Python 去重")
    blocks(d, data["judge"], 1168, 143, 4)
    d.rect(MARGIN, 1358, CONTENT, 116, fill="#2b3b57", stroke="#2b3b57", radius=14)
    for i, line in enumerate(data["footer"]):
        d.text(
            MARGIN + 22,
            1395 + i * 34,
            line,
            size=20 if i == 0 else 18,
            fill="#eef2fa",
            weight=600 if i == 0 else 400,
        )
    d.text(
        WIDTH - MARGIN,
        1510,
        "核对范围：用户提供的角色 / 执行设计截图 · 依据见 docs/design-implementation-status.md",
        size=15,
        fill=MUTED,
        anchor="end",
    )
    return svg(d, data["title"], 1536)


def component_diagram(data):
    d = Drawing()
    heading(d, data["component_title"], data["component_subtitle"], data["version"])
    cols = [MARGIN + 20, 285, 600, 940]
    d.rect(MARGIN, 180, CONTENT, 56, fill="#eaf0f8", stroke="#dce5f1", radius=10)
    for x, label in zip(cols, ["组件", "原推荐项目", "实现状态", "当前实现 / 差异"]):
        d.text(x, 216, label, size=21, weight=700)
    y = 246
    for i, item in enumerate(data["components"]):
        lines = [line for text in item["lines"] for line in wrap(text, WIDTH - MARGIN - cols[3] - 20, 18)]
        h = max(126, 41 + len(lines) * 27)
        d.rect(MARGIN, y, CONTENT, h, fill="white" if i % 2 == 0 else "#fbfcff", radius=10)
        for j, text in enumerate(wrap(item["component"], cols[1] - cols[0] - 20, 21)):
            d.text(cols[0], y + 42 + j * 30, text, size=21, weight=650)
        for j, text in enumerate(wrap(item["recommended"], cols[2] - cols[1] - 25, 21)):
            d.text(cols[1], y + 42 + j * 30, text, size=21)
        label = item.get("label")
        status(d, cols[2], y + 19, item["status"], label=label, size=16)
        if item["status"] == "alternative":
            d.text(cols[2], y + 80, "技术方案与原推荐不同", size=17, fill=MUTED)
        elif item["component"] == "工作流":
            d.text(cols[2], y + 80, "Python ✓   GitHub Actions ✓", size=16, fill=MUTED)
        for j, text in enumerate(lines):
            d.text(cols[3], y + 39 + j * 27, text, size=18, fill=MUTED)
        y += h + 12
    d.rect(MARGIN, y + 8, CONTENT, 122, fill="#2b3b57", stroke="#2b3b57", radius=14)
    d.text(MARGIN + 22, y + 46, "最需避免的误读", size=22, fill="white", weight=700)
    d.text(
        MARGIN + 22,
        y + 78,
        "自建探索循环 ≠ Browser Use 集成；报告 / 源码建议 ≠ Codex 修复闭环；CI 测试 ≠ 线上 UX 自动触发。",
        size=19,
        fill="#edf2fa",
    )
    d.text(
        MARGIN + 22,
        y + 108,
        "已有 severity / confidence 建议，不代表实现了数值评分体系或完成了人工校准。",
        size=18,
        fill="#d8e2f2",
    )
    d.text(
        WIDTH - MARGIN,
        y + 167,
        "依据：pyproject.toml · src/ai_ux · .github/workflows/ci.yml",
        size=15,
        fill=MUTED,
        anchor="end",
    )
    return svg(d, data["component_title"], y + 190)


async def main():
    data = json.loads((DIRECTORY / "design-status.json").read_text())
    outputs = {
        "design-roles-status": role_diagram(data),
        "design-components-status": component_diagram(data),
    }
    async with async_playwright() as p:
        options = {"headless": True}
        if os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
            options["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]
        else:
            options["channel"] = "chrome"
        browser = await p.chromium.launch(**options)
        page = await browser.new_page(viewport={"width": WIDTH, "height": 1200}, device_scale_factor=1.5)
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
