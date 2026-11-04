"""Capture real workbench pages with isolated, explicitly synthetic documentation data.

No provider calls, Git clones, public-demo actions, credentials or production artifacts
are used. The worker count is zero and the disposable database is removed on exit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from playwright.async_api import async_playwright, expect


def seed(base: Path):
    from ai_ux.jfcs62_web import create_jfcs62_app
    from ai_ux.report import render
    from ai_ux.store import Store
    from ai_ux.webapp import Settings

    epoch = datetime.now(timezone.utc).replace(microsecond=0).timestamp() - 3600
    store = Store(base / "workbench")
    job_id = "screenshot-demo-01"
    batch = store.directory / "jobs" / job_id / "demo-batch"
    run = batch / "search-example-01-demo"
    (run / "screenshots").mkdir(parents=True)
    (run / "observations").mkdir()
    request = {
        "repo": "https://github.com/example/catalog.git",
        "url": "https://catalog.example/",
        "goal": "界面演示：搜索商品，查看详情，确认结果反馈。",
        "tasks": 1,
        "repeats": 1,
        "max_steps": 12,
        "timeout": 300,
    }
    with store.connect() as db:
        for offset, status in [(0, "completed"), (1, "failed"), (2, "queued")]:
            ident = f"screenshot-demo-{offset + 1:02}"
            started = epoch - offset * 3600 if status != "queued" else None
            finished = started + 128 if started is not None else None
            db.execute(
                "INSERT INTO jobs(id,status,request,created_at,started_at,finished_at,stage,report_path,error) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    ident,
                    status,
                    json.dumps(request, ensure_ascii=False),
                    epoch - offset * 3600 - 3,
                    started,
                    finished,
                    status,
                    "demo-batch/report.html" if offset == 0 else None,
                    "演示错误：浏览器操作超时；现场已保留。" if status == "failed" else None,
                ),
            )
    events = [
        {"kind": "stage", "stage": "browser", "status": "running", "t_ms": 0},
        {
            "kind": "observation",
            "t_ms": 950,
            "step": 0,
            "url": request["url"],
            "screenshot": "screenshots/step-000.png",
            "note": "合成演示页面，非真实评测。",
        },
        {
            "kind": "model_call",
            "t_ms": 6200,
            "provider": "zhipu",
            "model": "glm-5.3-Flash",
            "purpose": "explore",
            "status": "ok",
            "duration_ms": 5100,
            "usage": {"prompt_tokens": 2400, "completion_tokens": 110, "total_tokens": 2510},
        },
        {
            "kind": "agent_action_start",
            "t_ms": 6300,
            "action": {
                "kind": "fill",
                "target": "search",
                "text": "无线耳机",
                "rationale": "示例操作说明：先输入关键词，查看匹配结果。",
            },
        },
        {"kind": "agent_action_result", "t_ms": 6480, "action_kind": "fill", "step": 0, "ok": True},
        {
            "kind": "agent_action_result",
            "t_ms": 7100,
            "action_kind": "click",
            "step": 1,
            "ok": False,
            "error": "Demo fixture: element not present in the latest observation",
        },
        {
            "kind": "verification",
            "t_ms": 9400,
            "result": {"task_outcome": "not_verified", "page_health": "passed"},
        },
        {"kind": "stage", "stage": "judge", "status": "running", "t_ms": 10000},
        {
            "kind": "model_call",
            "t_ms": 19800,
            "provider": "zhipu",
            "model": "glm-5.3-Flash",
            "purpose": "judge",
            "status": "ok",
            "duration_ms": 9800,
            "usage": {"prompt_tokens": 4900, "completion_tokens": 420, "total_tokens": 5320},
        },
    ]
    events[8:8] = [
        {
            "kind": "model_call",
            "t_ms": 11300 + i * 2400,
            "provider": "zhipu",
            "model": "glm-5.3-Flash",
            "purpose": "judge",
            "status": "error",
            "error": "invalid_json_schema",
            "attempt": i + 1,
            "duration_ms": 2400,
        }
        for i in range(3)
    ]
    for seq, event in enumerate(events):
        store.append_event(job_id, "demo-batch/" + run.name, seq, event, epoch + event["t_ms"] / 1000)
    (run / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
    (run / "observations/step-000.json").write_text(json.dumps(events[1], ensure_ascii=False))
    calls = [e for e in events if e["kind"] == "model_call"]
    (run / "model-calls.json").write_text(json.dumps(calls, ensure_ascii=False, indent=2))
    verification = {"task_outcome": "not_verified", "page_health": "passed", "checks": []}
    (run / "verification.json").write_text(json.dumps(verification))
    result = {
        "run_id": run.name,
        "task_id": "示例：搜索商品",
        "attempt": 1,
        "goal": request["goal"],
        "started_at": datetime.fromtimestamp(epoch, timezone.utc).isoformat(),
        "duration_ms": 128000,
        "stop_reason": "explorer_done",
        "judge_status": "completed",
        "verification": verification,
        "video": {"status": "disabled"},
        "judgement": {
            "summary": "演示结果：展示页面观察、操作意图、异常和最终验收。",
            "limitations": ["文档演示数据，不代表真实产品评测或模型性能。"],
        },
    }
    (run / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    render(
        batch,
        {
            "batch_id": batch.name,
            "app_name": "Catalog · 界面演示",
            "target_url": request["url"],
            "runs": [result],
            "issues": [],
        },
    )
    for i in range(2):
        store.append_event(
            "screenshot-demo-02",
            "demo",
            i,
            {"kind": "stage_error", "stage": "browser.click", "error": "Demo fixture: timeout 5000ms"},
            epoch - 3500 + i,
        )
    cluster = next(c for c in store.clusters(0, epoch + 1000) if c["category"] == "schema")
    store.triage([cluster["cluster_id"]], "investigating", "界面演示：核对输出结构与重试策略。")
    metadata = {
        "version": "screenshot-demo",
        "snapshot_sha256": "ab" * 32,
        "built_at": datetime.fromtimestamp(epoch, timezone.utc).isoformat(),
        "target": "local-demo",
        "operator": "demo",
    }
    (base / "release.json").write_text(json.dumps(metadata))
    (base / "state").mkdir(exist_ok=True)
    (base / "state/deployments.jsonl").write_text(
        json.dumps(
            {
                **metadata,
                "ts": metadata["built_at"],
                "status": "success",
                "selftest": "passed",
                "tests": "界面演示记录",
                "note": "示例数据，非实际发布验收。",
                "changes": [
                    "示例：分析回放与事件筛选",
                    "示例：工具错误聚类与样本定位",
                    "示例：用量统计和部署记录",
                ],
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    settings = Settings()
    settings.workers = 0
    return create_jfcs62_app(base, base / "release.json", settings), job_id, run


async def capture(output: Path, channel: str):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="uxpilot-docs-") as directory:
        base = Path(directory)
        os.environ["UXPILOT_DATA_DIR"] = str(base / "workbench")
        os.environ["UXPILOT_WORKERS"] = "0"
        app, job_id, run = seed(base)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(200):
                if server.started:
                    break
                await asyncio.sleep(0.025)
            if not server.started:
                raise RuntimeError("Screenshot preview failed to start")
            origin = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
            async with async_playwright() as p:
                browser = await p.chromium.launch(channel=channel, headless=True)
                page = await browser.new_page(viewport={"width": 1440, "height": 1050}, locale="zh-CN")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.set_content(
                    '<html lang="zh-CN"><meta charset="utf-8"><body style="margin:0;background:#f6f8fc;color:#263449;font:24px system-ui;padding:70px"><small style="color:#73859b">DOCUMENTATION FIXTURE · 界面演示</small><h1>Catalog</h1><input value="无线耳机" style="font:inherit;padding:16px;width:600px;border:1px solid #ccd6e3;border-radius:12px"><h2>找到 3 件商品</h2><div style="background:white;padding:30px;border-radius:16px;border:1px solid #dce5ee">示例商品 A · 无线耳机<br><p style="font-size:18px;color:#728198">此页面仅用于文档截图，不包含实际商品或用户数据。</p></div></body></html>'
                )
                await page.screenshot(path=str(run / "screenshots/step-000.png"))
                from ai_ux.replay import render_replay

                render_replay(run)
                await page.goto(origin)
                await page.locator("#repo").fill("https://github.com/example/catalog.git")
                await page.locator("#url").fill("https://catalog.example/")
                await page.locator("#goal").fill("搜索商品，进入详情页，确认筛选和结果反馈是否清楚。")
                await expect(page.locator("#recent table")).to_be_visible()
                await page.locator("h1").click()
                await page.screenshot(path=str(output / "home.png"))
                await page.goto(origin + "/jobs#" + job_id)
                await expect(page.get_by_role("link", name="打开分析回放")).to_be_visible()
                await page.locator("article details").nth(1).evaluate("el => el.open = true")
                await page.screenshot(path=str(output / "jobs.png"))
                await page.get_by_role("link", name="打开分析回放").click()
                await page.get_by_role("link", name="打开分析回放 →").click()
                await expect(page.locator("#play")).to_be_visible()
                await page.screenshot(path=str(output / "replay.png"))
                await page.locator("#next").click()
                for detail in await page.locator('.step[data-kind="observation"] details').all():
                    await detail.evaluate("el => el.open = true")
                await page.locator('.step[data-kind="observation"] img').evaluate("img => img.decode()")
                await page.locator('.step[data-kind="observation"]').scroll_into_view_if_needed()
                await page.screenshot(path=str(output / "replay-evidence.png"))
                await page.goto(origin + "/errors")
                await expect(page.locator(".error-card").first).to_be_visible()
                await page.screenshot(path=str(output / "errors.png"))
                await page.goto(origin + "/stats")
                await expect(page.get_by_role("heading", name="模型用量")).to_be_visible()
                await page.screenshot(path=str(output / "stats.png"), full_page=True)
                await page.goto(origin + "/deploys.html")
                await expect(page.locator("#release-state")).to_have_text("版本一致")
                await page.screenshot(path=str(output / "deploys.png"))
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.goto(origin)
                await expect(page.locator("#form")).to_be_visible()
                assert await page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
                await page.screenshot(path=str(output / "mobile-home.png"), full_page=True)
                await page.goto(origin + "/stats")
                await expect(page.get_by_role("heading", name="模型用量")).to_be_visible()
                assert await page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
                await page.locator("#models-table").scroll_into_view_if_needed()
                await page.screenshot(path=str(output / "mobile-stats.png"))
                assert not errors, errors
                await browser.close()
        finally:
            server.should_exit = True
            await asyncio.to_thread(thread.join, 10)
    print(f"Captured 9 UI screenshots in {output}; data is synthetic and no evaluations were executed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/screenshots"))
    parser.add_argument("--channel", default="chrome")
    args = parser.parse_args()
    asyncio.run(capture(args.output.resolve(), args.channel))


if __name__ == "__main__":
    main()
