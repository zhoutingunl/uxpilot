from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path

from .replay import render_batch_replay


def aggregate(runs: list[dict]):
    """Exact normalized issue matching; never inflate confirmation from retries of one run."""
    issues = {}
    for run in runs:
        for issue in (run.get("judgement") or {}).get("issues", []):
            key = (run["task_id"], issue["category"], re.sub(r"\W+", "", issue["title"]).lower())
            digest = hashlib.sha256(repr(key).encode()).hexdigest()[:10]
            entry = issues.setdefault(
                digest, {**issue, "id": digest, "task_id": run["task_id"], "run_ids": [], "evidence_refs": []}
            )
            entry["run_ids"].append(run["run_id"])
            entry["evidence_refs"].extend(f"{run['run_id']}/{ref}" for ref in issue["evidence_refs"])
    for issue in issues.values():
        issue["observed_runs"] = len(set(issue["run_ids"]))
        issue["attempts_for_task"] = sum(r["task_id"] == issue["task_id"] for r in runs)
        issue["status"] = (
            "candidate" if issue["observed_runs"] >= 2 and issue["confidence"] == "high" else "needs_review"
        )
    return list(issues.values())


def render(root: Path, report: dict):
    report["analysis_replay"] = "analysis-replay.html"
    for run in report["runs"]:
        run["analysis_replay"] = f"{run['run_id']}/replay.html"
    render_batch_replay(root, report)
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    lines = [
        f"# {report.get('app_name', 'Web 产品')} AI UX Agent 体验报告",
        "",
        f"[分析回放：人工审核全过程]({report.get('analysis_replay_url', 'analysis-replay.html')})",
        "",
        f"运行批次：{report['batch_id']}。目标：{report['target_url']}。",
        "",
        "这是自动探索和独立模型评审结果，候选问题仍需人工确认。任务验收与 UX 判断分开记录；合成语音不代表真人样本。",
        "",
        "| 任务 | 业务独立验收 | 页面基础检查 | Explorer 结束原因 | Judge | 证据 |",
        "|---|---|---|---|---|---|",
    ]
    for r in report["runs"]:
        judge = r.get("judge_status", "not_run")
        lines.append(
            f"| {r['task_id']} | {r.get('verification', {}).get('task_outcome', 'not_verified')} | {r.get('verification', {}).get('page_health', 'not_checked')} | {r['stop_reason']} | {judge} | [{r['run_id']}]({r['run_id']}/run.json) |"
        )
    if report.get("repository"):
        lines += [
            "",
            f"源码版本：{report['repository'].get('commit') or '非 Git 目录'}；工作区修改：{report['repository'].get('dirty')}。源码与部署版本关系未知。",
            "",
        ]
    lines += ["", "## 任务观察", ""]
    for r in report["runs"]:
        lines += [
            f"### {r['task_id']} · 第 {r['attempt']} 次",
            "",
            r["goal"],
            "",
            f"总耗时 {r['duration_ms'] / 1000:.1f} 秒；包含 Agent 决策等待，不等于产品延迟。",
            "",
        ]
        coverage = r.get("verification", {}).get("coverage_note")
        if coverage:
            lines += [coverage, ""]
        if r.get("judgement"):
            lines += [r["judgement"]["summary"], ""]
            lines += [f"- 限制：{s}" for s in r["judgement"]["limitations"]]
            lines += [""]
        if r.get("error"):
            lines += [f"运行错误：{r['error']}", ""]
        if speech := r.get("speech_review"):
            lines += [
                f"独立语音复核：{speech.get('status')} · {speech.get('model', '')}。",
                "",
                html.escape(speech.get("transcript", "")),
                "",
                f"[语音复核证据]({r['run_id']}/speech-review.json)",
                "",
            ]
        lines += [
            f"[分析回放]({r['run_id']}/replay.html) · [逐步事件]({r['run_id']}/events.jsonl) · [独立验收]({r['run_id']}/verification.json) · [模型调用]({r['run_id']}/model-calls.json)",
            "",
        ]
        if r.get("video", {}).get("status") == "ready":
            lines += [
                f"[无声录屏]({r['run_id']}/session.mp4)，{r['video']['duration_seconds']:.1f} 秒，2 fps；用于视觉状态与流程审查，不能替代动作高速细节或声音验收。",
                "",
            ]
        if r.get("video", {}).get("review"):
            lines += [
                f"[Judge 使用的视频]({r['run_id']}/review.mp4) · [剪辑时间映射]({r['run_id']}/video-timeline.json)，问题中的视频时间点对应此精简版。",
                "",
            ]
    lines += ["## 修改候选", ""]
    if not report["issues"]:
        lines += [
            "本批次未产出有合法证据引用的问题。请结合任务与 Judge 是否成功运行解读，不能据此断言没有问题。",
            "",
        ]
    for i in report["issues"]:
        lines += [
            f"### {i['severity']} · {i['title']}",
            "",
            f"状态：{i['status']}；该任务 {i['attempts_for_task']} 次运行中 {i['observed_runs']} 次出现同名候选。",
            "",
            "**观察事实**",
            "",
        ] + [f"- {s}" for s in i["observations"]]
        lines += [
            "",
            f"**判断：** {i['inference']}",
            "",
            f"**建议：** {i['recommendation']}",
            "",
            "**复现步骤**",
            "",
        ]
        lines += [f"{n + 1}. {s}" for n, s in enumerate(i["reproduction_steps"])]
        lines += ["", "**回归验收**", ""] + [f"- {s}" for s in i["acceptance"]]
        lines += ["", "证据：" + " · ".join(f"[{ref}]({ref})" for ref in i["evidence_refs"]), ""]
        if i.get("video_timestamps"):
            lines += ["视频时间点：" + "、".join(i["video_timestamps"]), ""]
        if i.get("source_locations"):
            lines += ["源码候选位置（已校验文件、行号和引用原文；根因与部署版本仍需确认）：", ""]
            for loc in i["source_locations"]:
                lines += [
                    f"- `{loc['path']}:{loc['line']}`：{loc['relevance']} 修改建议：{loc['suggested_change']}"
                ]
            lines += [i.get("source_limitation", ""), ""]
    lines += [
        "## 模型和覆盖边界",
        "",
        "- GLM-5.3 Flash 为主，MiniMax-M3.1 Flash 为备用；每次实际调用与回退原因保存在 model-calls.json。",
        "- Explorer 只接收任务、页面观察与截图；Judge 使用新的上下文、验证结果与视频，没有接收业务源码。",
        "- 仅执行配置中的有界操作；外部 Issue、修复、合并和部署不在本版自动流程中。",
        "- 单次候选不计为稳定复现；同一任务重复运行才可提升候选状态。",
        "",
    ]
    markdown = "\n".join(lines)
    (root / "report.md").write_text(markdown)
    # Minimal standalone report, escaping all model/user strings; links served locally, no external assets.
    body = [f"<h1>AI UX Agent 体验报告</h1><p>{html.escape(report['batch_id'])}</p>"]
    replay_href = report.get("analysis_replay_url") or "analysis-replay.html"
    body.append(f'<p><a href="{html.escape(replay_href, quote=True)}">分析回放 · 人工审核过程</a></p>')
    body.append('<p><a href="report.md">Markdown 报告</a> · <a href="report.json">JSON 报告</a></p>')
    for r in report["runs"]:
        rid = html.escape(r["run_id"], quote=True)
        body.append(
            f"<section><h2>{html.escape(r['task_id'])}</h2><p>{html.escape(r.get('judgement', {}).get('summary', r['stop_reason']))}</p>"
        )
        if r.get("video", {}).get("status") == "ready":
            video_name = "review.mp4" if r.get("video", {}).get("review") else "session.mp4"
            body.append(f'<video controls preload="metadata" src="{rid}/{video_name}"></video>')
            if video_name == "review.mp4":
                body.append(
                    f'<p>评审精简版；<a href="{rid}/session.mp4">原始完整视频</a> · <a href="{rid}/video-timeline.json">时间映射</a></p>'
                )
        if speech := r.get("speech_review"):
            body.append(
                f"<h3>独立语音复核：{html.escape(speech.get('status', 'unknown'))}</h3>"
                f"<blockquote>{html.escape(speech.get('transcript', ''))}</blockquote>"
                f'<p><a href="{rid}/speech-review.json">ASR 证据</a></p>'
            )
            if speech.get("status") == "completed":
                body.append(f'<audio controls preload="metadata" src="{rid}/response.wav"></audio>')
        body.append(
            f'<p><a href="{rid}/replay.html">分析回放</a> · <a href="{rid}/run.json">运行证据</a> · <a href="{rid}/verification.json">验收</a></p></section>'
        )
    for i in report["issues"]:
        body.append(
            f"<section><h2>{html.escape(i['severity'] + ' · ' + i['title'])}</h2><p>{html.escape(i['inference'])}</p><p>{html.escape(i['recommendation'])}</p><ul>"
        )
        body += [f"<li>{html.escape(s)}</li>" for s in i["observations"]]
        body.append("</ul>")
        if i.get("source_locations"):
            body.append("<p>源码候选位置（不等于已证明根因）：</p><ul>")
            for loc in i["source_locations"]:
                body.append(
                    f"<li><code>{html.escape(loc['path'])}:{loc['line']}</code> {html.escape(loc['relevance'])}；{html.escape(loc['suggested_change'])}</li>"
                )
            body.append("</ul>")
        body.append("</section>")
    doc = (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI UX Agent 报告</title><style>body{max-width:1100px;margin:40px auto;padding:0 20px;font:16px/1.7 system-ui;color:#203026;background:#f6f7f2}section{background:white;padding:24px;margin:22px 0;border-radius:12px}video{max-width:100%;max-height:600px}a{color:#245d81}h2{font-size:22px}</style>'
        + "".join(body)
        + "</html>"
    )
    (root / "report.html").write_text(doc)
