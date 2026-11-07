"""Deterministic DuckKing language-menu regression, separate from UX judgement."""

from __future__ import annotations

import html
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin

from .browser import BrowserRuntime
from .evidence import Evidence
from .policy import scrub
from .schema import Task

PAGES = [
    "index.html",
    "scenarios.html",
    "demos.html",
    "studio.html",
    "training.html",
    "balance.html",
    "seesaw.html",
    "statistics.html",
    "experience.html",
    "deploys.html",
]
HOME = {"zh": "首页", "en": "Home", "fr": "Accueil"}
SNAPSHOT = """() => {
 const button=document.querySelector('#lang-switch-button'),rect=button?.getBoundingClientRect();
 const mode=document.querySelector('#voice-mode');
 return {language:document.documentElement.lang.split('-')[0],origin:performance.timeOrigin,
 room:new URL(location.href).searchParams.get('room'),
 home:document.querySelector('.site-topbar nav a[href*="index.html"]')?.textContent.trim(),
 selected:[...document.querySelectorAll('.lang-menu [aria-current=true]')].map(e=>e.dataset.lang),
 language_button_in_view:!!rect&&rect.width>0&&rect.top>=0&&rect.bottom<=innerHeight&&rect.left>=0&&rect.right<=innerWidth,
 no_horizontal_overflow:document.documentElement.scrollWidth<=innerWidth+1,
 voice_mode:mode?.value,local_disabled:mode?.querySelector('[value=local_first]')?.disabled,
 has_canvas:!!document.querySelector('canvas'),same_canvas:!window.__uxLocaleCanvas||window.__uxLocaleCanvas===document.querySelector('canvas')};
}"""


async def settled(page, language):
    await page.wait_for_function(
        """([lang,home])=>document.documentElement.lang.split('-')[0]===lang&&
        document.querySelector('.site-topbar nav a[href*="index.html"]')?.textContent.trim()===home""",
        arg=[language, HOME[language]],
        timeout=10000,
    )
    await page.wait_for_timeout(250)
    await page.wait_for_function(
        "() => {const mode=document.querySelector('#voice-mode');return !mode||!mode.disabled}",
        timeout=10000,
    )


def checks_for(snapshot, language):
    checks = {
        "document_language": snapshot.get("language") == language,
        "localized_home_link": snapshot.get("home") == HOME[language],
        "selected_language": snapshot.get("selected") == [language],
        "language_button_in_view": snapshot.get("language_button_in_view") is True,
        "no_horizontal_overflow": snapshot.get("no_horizontal_overflow") is True,
    }
    if snapshot.get("voice_mode") is not None and language != "zh":
        checks["cloud_mode_for_non_chinese"] = (
            snapshot["voice_mode"] == "cloud" and snapshot.get("local_disabled") is True
        )
    return checks


def render(root, report):
    (root / "locale-report.json").write_text(json.dumps(scrub(report), ensure_ascii=False, indent=2) + "\n")
    rows = []
    for case in report["cases"]:
        failed = ", ".join(k for k, v in case.get("checks", {}).items() if v is False)
        screenshot = case.get("screenshot")
        link = f'<a href="{html.escape(screenshot, quote=True)}">截图</a>' if screenshot else ""
        rows.append(
            "<tr>"
            + "".join(
                f"<td>{html.escape(str(v))}</td>"
                for v in [
                    case["viewport"],
                    case["page"],
                    case["language"],
                    case["phase"],
                    case["status"],
                    failed or case.get("error", ""),
                ]
            )
            + f"<td>{link}</td></tr>"
        )
    body = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
    <title>DuckKing 多语言切换验收</title><style>body{font:15px system-ui;max-width:1400px;margin:32px auto;padding:0 20px;color:#182820}
    table{border-collapse:collapse;width:100%}th,td{text-align:left;border-bottom:1px solid #ddd;padding:10px}a{color:#176342}</style>
    <h1>DuckKing 多语言切换验收</h1>"""
    body += f"<p>结果：{html.escape(report['status'])}；{len(report['cases'])} 个检查场景。目标：{html.escape(report['target_url'])}</p>"
    body += "<p>通过真实语言菜单切换，核对关键界面文案、房间、页面与画布保持、移动端布局、刷新及跨页保持。此报告不代表所有文案翻译准确或语音已经验收。</p>"
    body += (
        "<table><thead><tr><th>视口</th><th>页面</th><th>语言</th><th>阶段</th><th>结果</th><th>失败项 / 错误</th><th>证据</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></html>"
    )
    (root / "locale-report.html").write_text(body)


async def check_locales(config, *, output=None, pages=None, viewports=None):
    if config.app.adapter != "duckking":
        raise ValueError("check-locales requires the duckking adapter")
    pages = pages or PAGES
    if not pages or any(p not in PAGES for p in pages):
        raise ValueError("unsupported locale-check page")
    viewports = viewports or ["desktop", "mobile"]
    if any(v not in {"desktop", "mobile"} for v in viewports):
        raise ValueError("unsupported viewport")
    root = Path(output or config.output_dir) / (
        "locales-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    )
    root.mkdir(parents=True, exist_ok=False)
    report = {
        "target_url": config.app.base_url,
        "status": "running",
        "cases": [],
        "runtimes": [],
        "scope": "zh/en/fr menu, key UI copy, reload/cross-page persistence, room/canvas preservation and layout; not full translation or speech accuracy",
    }
    for viewport in viewports:
        evidence = Evidence(root / viewport)
        browser = config.browser.model_copy(
            update={"width": 390, "height": 844} if viewport == "mobile" else {}
        )
        runtime = BrowserRuntime(
            config.app,
            browser,
            Task(id="locale-" + viewport, goal="多语言菜单回归", start_path="/?lang=zh"),
            evidence,
        )
        step, expected = 0, "zh"
        try:
            await runtime.start()
            for page_name in pages:
                url = (
                    urljoin(config.app.base_url, page_name)
                    + "?"
                    + urlencode({"room": runtime.owned_room, "measurement": "verification"})
                )
                try:
                    await runtime.page.goto(
                        runtime.policy.check_url(url), wait_until="domcontentloaded", timeout=30000
                    )
                    await settled(runtime.page, expected)
                    inherited = await runtime.page.evaluate(SNAPSHOT)
                    inherited_checks = {
                        "cross_page_language": inherited["language"] == expected,
                        "same_room": inherited["room"] == runtime.owned_room,
                    }
                    evidence.event(
                        "locale_check", page=page_name, phase="navigation", checks=inherited_checks
                    )
                    report["cases"].append(
                        {
                            "viewport": viewport,
                            "page": page_name,
                            "language": expected,
                            "phase": "navigation",
                            "checks": inherited_checks,
                            "status": "passed" if all(inherited_checks.values()) else "failed",
                        }
                    )
                except Exception as error:
                    report["cases"].append(
                        {
                            "viewport": viewport,
                            "page": page_name,
                            "language": expected,
                            "phase": "navigation",
                            "status": "error",
                            "error": type(error).__name__,
                        }
                    )
                    try:
                        observation = await runtime.observe(step)
                        step += 1
                        report["cases"][-1]["screenshot"] = f"{viewport}/{observation['screenshot']}"
                    except Exception:
                        pass
                    continue
                for language in HOME:
                    phase = "switch"
                    case = {"viewport": viewport, "page": page_name, "language": language, "phase": phase}
                    try:
                        before = await runtime.page.evaluate(SNAPSHOT)
                        await runtime.page.evaluate(
                            "() => {window.__uxLocaleCanvas=document.querySelector('canvas');scrollTo(0,0)}"
                        )
                        await runtime.page.locator("#lang-switch-button").click()
                        await runtime.page.locator(f'.lang-menu [data-lang="{language}"]').click()
                        await settled(runtime.page, language)
                        snapshot = await runtime.page.evaluate(SNAPSHOT)
                        checks = checks_for(snapshot, language)
                        checks.update(
                            same_document=snapshot["origin"] == before["origin"],
                            same_room=snapshot["room"] == runtime.owned_room,
                        )
                        if before["has_canvas"]:
                            checks["canvas_preserved"] = snapshot["same_canvas"]
                        observation = await runtime.observe(step)
                        step += 1
                        case.update(
                            checks=checks,
                            snapshot=snapshot,
                            status="passed" if all(checks.values()) else "failed",
                            screenshot=f"{viewport}/{observation['screenshot']}",
                        )
                        report["cases"].append(case)
                        evidence.event(
                            "locale_check", page=page_name, language=language, phase=phase, checks=checks
                        )
                        phase = "reload"
                        await runtime.page.reload(wait_until="domcontentloaded", timeout=30000)
                        await settled(runtime.page, language)
                        snapshot = await runtime.page.evaluate(SNAPSHOT)
                        checks = checks_for(snapshot, language)
                        checks["same_room"] = snapshot["room"] == runtime.owned_room
                        observation = await runtime.observe(step)
                        step += 1
                        report["cases"].append(
                            {
                                "viewport": viewport,
                                "page": page_name,
                                "language": language,
                                "phase": phase,
                                "checks": checks,
                                "snapshot": snapshot,
                                "status": "passed" if all(checks.values()) else "failed",
                                "screenshot": f"{viewport}/{observation['screenshot']}",
                            }
                        )
                        evidence.event(
                            "locale_check", page=page_name, language=language, phase=phase, checks=checks
                        )
                        expected = language
                    except Exception as error:
                        report["cases"].append(
                            {
                                "viewport": viewport,
                                "page": page_name,
                                "language": language,
                                "phase": phase,
                                "status": "error",
                                "error": type(error).__name__,
                            }
                        )
                render(root, report)
                print(f"[{viewport}] {page_name}: checked zh/en/fr", flush=True)
        except Exception as error:
            report["cases"].append(
                {
                    "viewport": viewport,
                    "page": "startup",
                    "language": "zh",
                    "phase": "startup",
                    "status": "error",
                    "error": type(error).__name__,
                }
            )
        finally:
            await runtime.finish()
            evidence.manifest()
            report["runtimes"].append(
                {"viewport": viewport, "cleanup": runtime.cleanup, "video": runtime.video_info}
            )
    report["status"] = (
        "passed" if report["cases"] and all(c["status"] == "passed" for c in report["cases"]) else "failed"
    )
    render(root, report)
    print(f"Locale report: {root / 'locale-report.html'}", flush=True)
    return root, report
