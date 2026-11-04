"""Portable analysis replay pages built from real run evidence, no hidden chain-of-thought."""

from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import quote

from .policy import scrub

STYLE = """.replay-controls{position:sticky;top:0;z-index:5;display:flex;gap:8px;align-items:center;flex-wrap:wrap;box-shadow:0 3px 12px #0001}.replay-controls button,.replay-controls select{font:inherit;padding:6px 10px;border:1px solid #b8c8d8;background:white;border-radius:5px;cursor:pointer}#play{background:#286b94;color:white}#counter{margin-left:auto}.replay-current{outline:2px solid #286b94}.replay-future{opacity:.5}[hidden]{display:none!important}body{font:15px/1.65 system-ui;color:#233344;background:#f4f7fb;max-width:1180px;margin:30px auto;padding:0 20px}a{color:#286b94}h1{font-size:30px}header,section,article{background:white;padding:20px 24px;border:1px solid #dce5ee;border-radius:12px;margin:14px 0}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f8fb;padding:14px;border-radius:8px;font-size:12px}img,video{max-width:100%;max-height:480px}nav{display:flex;gap:18px;flex-wrap:wrap}.meta{color:#697a8c;font-size:13px}.error{border-left:4px solid #c75b44}.ok{border-left:4px solid #2b947a}.tag{font:12px monospace;background:#e8eff7;padding:4px 8px;border-radius:5px}.step{scroll-margin-top:15px}details+details{margin-top:12px}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:9px;border-bottom:1px solid #e5ebf1}"""


def escape(value):
    return html.escape(str(value), quote=True)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def read_events(path: Path):
    events = []
    try:
        for line in path.read_text().splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return events


def link(root: Path, relative: str, label: str):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        return ""
    return f'<a href="{quote(relative, safe="/#-._")}">{escape(label)}</a>'


def page(title, body):
    return f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title><style>{STYLE}</style>{body}</html>'


def details(label, value):
    return f"<details><summary>{escape(label)}</summary><pre>{escape(json.dumps(scrub(value), ensure_ascii=False, indent=2))}</pre></details>"


def render_replay(root: Path, run: dict | None = None):
    run = run or read_json(root / "run.json", {})
    events = (
        read_events(root / "imported-events.jsonl")
        if (root / "imported-events.jsonl").is_file()
        else read_events(root / "events.jsonl")
    )
    body = f'<header><h1>分析回放 · {escape(run.get("task_id", root.name))}</h1><p>{escape(run.get("goal", ""))}</p><nav>{link(root, "run.json", "运行结果")}{link(root, "verification.json", "独立验收")}{link(root, "events.jsonl", "事件 JSONL")}{link(root, "model-calls.json", "模型调用")}</nav><p class="meta">展示观察、操作目的、调用结果与证据；不包含模型内部思维链。时间相对本次运行，包含模型决策等待。</p></header>'
    body += f"<section><b>运行状态：</b>{escape(run.get('stop_reason', '进行中'))} · Judge：{escape(run.get('judge_status', '待评审'))}"
    for name, label in [("judgement.json", "UX 评审结果"), ("source-map.json", "源码定位候选")]:
        value = read_json(root / name)
        if value is not None:
            body += details(label, value)
    body += "</section>"
    if (root / "review.mp4").is_file():
        body += (
            '<section><h2>浏览器回放</h2><video controls preload="metadata" src="review.mp4"></video><nav>'
            + link(root, "session.mp4", "原始完整视频")
            + link(root, "video-timeline.json", "精简视频时间映射")
            + '</nav><p class="meta">无声录屏；产品耗时以事件和资源计时为准。</p></section>'
        )
    if events:
        body += '<section class="replay-controls"><button id="first" title="第一步">⏮</button><button id="prev" title="上一步">◀</button><button id="play">▶ 播放</button><button id="next" title="下一步">▶</button><button id="last" title="最后一步">⏭</button><label>筛选 <select id="filter"><option value="all">全部记录</option><option value="error">仅错误</option><option value="model_call">模型调用</option><option value="agent_action_start">浏览器操作</option><option value="observation">页面观察</option></select></label><span id="counter" aria-live="polite"></span></section><p class="meta">按记录逐步浏览；播放不会重新执行浏览器操作。</p>'
    for idx, event in enumerate(events):
        kind = event.get("kind", "unknown")
        failed = (
            event.get("ok") is False
            or event.get("status") == "error"
            or kind in {"stage_error", "pageerror", "http_error", "request_failed", "cleanup_error"}
        )
        title = event.get("stage") or event.get("action_kind") or event.get("purpose") or kind
        body += f'<article id="event-{idx}" class="step {"error" if failed else "ok"}" data-kind="{escape(kind)}"><span class="tag">{idx + 1:03}</span> <b>{escape(title)}</b> <span class="meta">+{event.get("t_ms", 0) / 1000:.2f}s · {escape(kind)}</span>'
        if kind == "agent_action_start":
            body += f"<p>{escape(event.get('action', {}).get('rationale', ''))}</p>"
        body += details("输入 / 输出 / 结果", event)
        screenshot = event.get("screenshot")
        if isinstance(screenshot, str) and link(root, screenshot, "截图"):
            body += f'<details><summary>当时的页面截图</summary><img loading="lazy" src="{quote(screenshot, safe="/-._")}" alt="步骤截图"></details>'
        body += "</article>"
    if not events:
        body += "<section>尚无事件记录。任务可能在浏览器启动前中断。</section>"
    body += "<section><h2>逐步页面观察</h2>"
    for path in sorted((root / "observations").glob("*.json")):
        data = read_json(path, {})
        ref = str(path.relative_to(root))
        body += details(f"步骤 {data.get('step', '?')} · {data.get('url', '')}", data)
        body += link(root, ref, "观察 JSON")
    body += "</section>"
    if events:
        body += '<script src="/static/replay.js"></script>'
    (root / "replay.html").write_text(page("UX Pilot 分析回放", body))
    return "replay.html"


def render_batch_replay(root, report):
    body = f'<header><h1>评测分析回放</h1><p>{escape(report.get("app_name", ""))} · {escape(report["batch_id"])}</p><nav><a href="report.html">体验报告</a><a href="report.json">结构化结果</a></nav></header>'
    planning = report.get("planning_replay")
    if planning in {"planning/replay.html", "../../discovery/replay.html"}:
        body += f'<section><h2>任务规划</h2><a href="{planning}">打开规划回放 →</a></section>'
    for run in report["runs"]:
        relative = f"{run['run_id']}/replay.html"
        target = root / run["run_id"]
        if target.is_dir():
            render_replay(target, run)
        body += f'<section><h2>{escape(run["task_id"])}</h2><p>{escape(run.get("goal", ""))}</p><p>{escape(run["stop_reason"])} · {escape(run.get("judge_status", ""))}</p><a href="{quote(relative, safe="/-._")}">打开分析回放 →</a></section>'
    (root / "analysis-replay.html").write_text(page("评测分析回放", body))
