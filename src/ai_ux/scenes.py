"""Explicit DuckKing scene lifecycle checks; no Explorer or speech API calls."""

from __future__ import annotations

import asyncio
import html
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .agent import judge_run
from .browser import BrowserRuntime
from .evidence import Evidence
from .llm import LLM
from .policy import safe_url, scrub
from .schema import Task

CASES = {
    "release": ("/scenarios.html?lang=zh", "启动踢球场景，释放场景后应退出加载状态，并可重新启动。"),
    "duel": ("/demos.html?lang=zh", "以预录对白演出第七幕；完成后两套独立的三维场景应同时保留。"),
}
SNAPSHOT = """() => {
 const shown=e=>!!e&&e.checkVisibility()&&e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0;
 const canvases=s=>[...document.querySelectorAll(s+' canvas')].filter(shown).length;
 const placeholder=document.querySelector('#scene-placeholder');
 let proof=null;try{proof=JSON.parse(document.querySelector('#theatre-result')?.textContent||'null')}catch{}
 let timing=null;try{timing=JSON.parse(document.querySelector('#physics-viewport')?.dataset.modelTiming||'null')}catch{}
 return {left:canvases('#demo-viewport'),right:canvases('#demo-second'),proof,timing,
 placeholder_visible:shown(placeholder),placeholder:placeholder?.textContent.trim(),
 progress_visible:[...document.querySelectorAll('#scene-placeholder progress')].some(shown),
 scene_title:document.querySelector('#scene-title')?.textContent.trim(),
 status:document.querySelector('#sim-status')?.textContent.trim(),
 release_disabled:document.querySelector('#release-scene')?.disabled,
 same_canvases:!window.__uxSceneCanvases||window.__uxSceneCanvases.every(c=>c.isConnected&&shown(c))};
}"""


class SceneRuntime(BrowserRuntime):
    """Allow exactly one observed secondary allocation for the duet check only."""

    def __init__(self, *args, duet=False):
        super().__init__(*args)
        self.duet = duet
        self.claim_lock = asyncio.Lock()

    async def _route(self, route):
        path = urlsplit(route.request.url).path.rstrip("/")
        # These checks cannot invoke Lingxi, even if a UI regression selects live mode.
        if path.startswith(("/api/voice", "/api/linx")):
            await route.abort("blockedbyclient")
            self.evidence.event("cloud_call_blocked", url=safe_url(route.request.url))
            return
        if self.duet and self.owned_room and route.request.method == "POST" and path == "/api/rooms/claim":
            async with self.claim_lock:
                try:
                    self.policy.check_url(route.request.url)
                    if self.policy.additional_rooms:
                        raise ValueError("secondary room budget exhausted")
                    response = await route.fetch(max_redirects=0)
                    if response.ok:
                        room = (await response.json()).get("room")
                        if not isinstance(room, str) or not room.isdecimal() or room == self.owned_room:
                            raise ValueError("invalid secondary allocation")
                        self.policy.additional_rooms.add(room)
                        self.evidence.event("secondary_room_allocated", room=room)
                    await route.fulfill(response=response)
                    return
                except ValueError:
                    self.evidence.event("policy_block", url=safe_url(route.request.url))
                    await route.abort("blockedbyclient")
                    return
        await super()._route(route)

    async def _ws_route(self, route):
        if urlsplit(route.url).path.rstrip("/") != "/scene-ws":
            self.evidence.event("cloud_call_blocked", url=safe_url(route.url))
            await route.close(code=1008, reason="scripted scene check only")
            return
        await super()._ws_route(route)


async def capture(runtime, phase):
    snapshot = await runtime.page.evaluate(SNAPSHOT)
    observation = await runtime.observe(len(runtime.evidence.observations))
    runtime.evidence.event("scene_checkpoint", phase=phase, snapshot=snapshot)
    return {"phase": phase, "snapshot": snapshot, "screenshot": observation["screenshot"]}


async def ready(page, timeout):
    await page.wait_for_function(
        """() => {const e=document.querySelector('#physics-viewport');
        try{return JSON.parse(e?.dataset.modelTiming||'{}').result==='ready'&&
        document.querySelector('#scene-placeholder')?.hidden===true&&
        document.querySelector('#release-scene')?.disabled===false}catch{return false}}""",
        timeout=timeout * 1000,
    )


def idle(snapshot):
    return (
        snapshot.get("scene_title") == "等待启动"
        and snapshot.get("release_disabled") is True
        and not snapshot.get("progress_visible")
        and not any(t in (snapshot.get("placeholder") or "") for t in ["第一帧", "已等待", "停止等待"])
    )


async def release_check(runtime, case, timeout, hold_seconds):
    page = runtime.page
    await page.locator('[data-scene="ball"]').click()
    await ready(page, timeout)
    case["checkpoints"].append(await capture(runtime, "loaded"))
    runtime.evidence.event("agent_action_start", action={"kind": "click", "target": "release-scene"})
    async with page.expect_response(
        lambda r: urlsplit(r.url).path == "/api/scenes/close" and r.request.method == "POST"
    ) as pending:
        await page.locator("#release-scene").click()
    response = await pending.value
    case["checks"]["release_request_succeeded"] = response.ok
    if not response.ok:
        case["status"] = "failed"
        return
    try:
        await page.wait_for_function(
            "() => document.querySelector('#scene-title')?.textContent.trim()==='等待启动'",
            timeout=10000,
        )
    except Exception:
        case["checks"]["returned_to_idle"] = False
    samples = []
    for i in range(3):
        checkpoint = await capture(runtime, "released-" + str(i))
        case["checkpoints"].append(checkpoint)
        samples.append(idle(checkpoint["snapshot"]))
        if i < 2:
            await page.wait_for_timeout(hold_seconds * 500)
    case["checks"]["idle_without_stale_loading"] = all(samples)
    # Recovery is a separate assertion: a later successful reload must not erase
    # an observed stale loading state after release.
    await page.locator('[data-scene="ball"]').click()
    try:
        await ready(page, timeout)
        case["checks"]["can_restart"] = True
    except Exception:
        case["checks"]["can_restart"] = False
    case["checkpoints"].append(await capture(runtime, "restarted"))
    runtime.evidence.event("agent_action_end")


async def duel_check(runtime, case, timeout, hold_seconds):
    page = runtime.page
    await page.locator("#demo-mode").select_option("rehearsal")
    runtime.evidence.event("agent_action_start", action={"kind": "click", "target": "duel"})
    await page.locator('[data-demo="duel"]').click()
    deadline = asyncio.get_running_loop().time() + timeout
    seen_both = False
    while asyncio.get_running_loop().time() < deadline:
        snapshot = await page.evaluate(SNAPSHOT)
        if snapshot["left"] == snapshot["right"] == 1 and not seen_both:
            seen_both = True
            await page.evaluate(
                "window.__uxSceneCanvases=[...document.querySelectorAll('.stage-worlds canvas')]"
            )
            case["checkpoints"].append(await capture(runtime, "both-during-performance"))
        if snapshot["proof"]:
            break
        await page.wait_for_timeout(500)
    case["checks"]["both_present_during_performance"] = seen_both
    case["checks"]["scripted_performance_completed"] = bool(
        snapshot["proof"]
        and snapshot["proof"].get("scene") == "duel"
        and snapshot["proof"].get("mode") == "scripted_rehearsal"
        and snapshot["proof"].get("status") == "completed"
    )
    if not case["checks"]["scripted_performance_completed"]:
        case["status"] = "failed" if seen_both else "blocked"
        case["error"] = "演出未完成，不能验收结束后的双场景保留。检查容量、加载和页面错误证据。"
    retained = []
    for i in range(3):
        checkpoint = await capture(runtime, "after-performance-" + str(i))
        case["checkpoints"].append(checkpoint)
        s = checkpoint["snapshot"]
        retained.append(s["left"] == s["right"] == 1 and s["same_canvases"])
        if i < 2:
            await page.wait_for_timeout(hold_seconds * 500)
    case["checks"]["both_retained_after_completion"] = (
        all(retained) and case["checks"]["scripted_performance_completed"]
    )
    runtime.evidence.event("agent_action_end")


async def check_scenes(config, *, output=None, cases=None, judge=False, timeout=150, hold_seconds=5):
    if config.app.adapter != "duckking":
        raise ValueError("check-scenes requires the duckking adapter")
    cases = list(CASES) if cases is None else cases
    if not cases or any(c not in CASES for c in cases):
        raise ValueError("unsupported scene check")
    root = Path(
        output
        or Path(config.output_dir)
        / ("scenes-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6])
    )
    root.mkdir(parents=True, exist_ok=False)
    report = {
        "target_url": config.app.base_url,
        "status": "running",
        "cases": [],
        "scope": "scene release/restart and scripted duet completion; canvas presence is structural evidence, visual contents require review",
        "judge_policy": "primary provider only; no fallback or speech service",
        "hold_seconds": hold_seconds,
    }
    for name in cases:
        path, goal = CASES[name]
        task = Task(id="scene-" + name, start_path=path, goal=goal)
        evidence = Evidence(root / name)
        runtime = SceneRuntime(config.app, config.browser, task, evidence, duet=name == "duel")
        case = {"id": name, "status": "running", "checks": {}, "checkpoints": []}
        try:
            await runtime.start()
            if "deployment" not in report:
                try:
                    response = await runtime.context.request.get(
                        config.app.base_url.rstrip("/") + "/api/health", timeout=10000
                    )
                    data = await response.json()
                    report["deployment"] = data.get("release")
                except Exception:
                    report["deployment"] = None
            print(f"Scene check: {name}", flush=True)
            if name == "release":
                await release_check(runtime, case, timeout, hold_seconds)
            else:
                await duel_check(runtime, case, timeout, hold_seconds)
            if case["status"] == "running":
                case["status"] = "passed" if all(case["checks"].values()) else "failed"
        except Exception as exc:
            case.update(
                status="failed" if False in case["checks"].values() else "blocked",
                error=f"{type(exc).__name__}: {str(exc)[:500]}",
            )
            if runtime.page and not runtime.page.is_closed():
                try:
                    case["checkpoints"].append(await capture(runtime, "blocked"))
                except Exception:
                    case["screenshot_error"] = "page unavailable"
        finally:
            try:
                await runtime.finish()
            except Exception as exc:
                case["cleanup_error"] = type(exc).__name__
        case["cleanup"] = runtime.cleanup
        verification = {
            "task_outcome": case["status"],
            "page_health": "not_checked",
            "coverage_note": report["scope"],
            "checks": case["checks"],
        }
        evidence.write("verification.json", verification)
        evidence.write("task.json", task.model_dump())
        if judge:
            # Deliberately omit config.speech and the configured fallback.
            models = config.models.model_copy(update={"fallback": config.models.primary})
            llm = LLM(models, on_call=lambda c: evidence.event("model_call", **c))
            try:
                judgement, call = await judge_run(llm, evidence, task, verification, runtime.video_info)
                case.update(judgement=judgement, judge_status="completed", judge_model=call)
            except Exception as exc:
                case.update(judge_status="failed", judge_error=str(exc)[:500])
            finally:
                await llm.close()
            case["model_calls"] = llm.calls
            evidence.write("model-calls.json", llm.calls)
        evidence.write("scene-result.json", case)
        evidence.manifest()
        report["cases"].append(case)
        render(root, report)
    statuses = {c["status"] for c in report["cases"]}
    report["status"] = "failed" if "failed" in statuses else "blocked" if "blocked" in statuses else "passed"
    if judge:
        report["judge_status"] = (
            "completed" if all(c.get("judge_status") == "completed" for c in report["cases"]) else "failed"
        )
    render(root, report)
    print(f"Report: {root / 'scene-report.html'}", flush=True)
    return root, report


def render(root, report):
    (root / "scene-report.json").write_text(json.dumps(scrub(report), ensure_ascii=False, indent=2) + "\n")
    body = '<!doctype html><meta charset="utf-8"><title>DuckKing 场景生命周期回归</title><style>body{font:16px system-ui;max-width:1000px;margin:32px auto}li{margin:12px 0}</style><h1>DuckKing 场景生命周期回归</h1>'
    body += f"<p>{html.escape(report['status'])} — {html.escape(report['target_url'])}</p>"
    body += "<p>只覆盖释放、重新启动和第七幕预录演出的完成状态；未验收语音及其他节目。截图和完整录屏可核对场景内容。blocked 表示未完成验收。</p>"
    for case in report["cases"]:
        name = case["id"]
        body += f"<h2>{name}: {case['status']}</h2><pre>{html.escape(json.dumps(case['checks'], ensure_ascii=False, indent=2))}</pre>"
        body += f"<p>{html.escape(case.get('error', ''))}</p><ul>"
        for checkpoint in case["checkpoints"]:
            body += (
                f'<li><a href="{name}/{checkpoint["screenshot"]}">{html.escape(checkpoint["phase"])}</a></li>'
            )
        body += f'</ul><p><a href="{name}/session.mp4">完整录屏</a> · <a href="{name}/scene-result.json">完整结果及模型评审</a></p>'
        if case.get("judgement"):
            body += f"<p>{html.escape(case['judgement']['summary'])}</p>"
        if case.get("judge_error"):
            body += f"<p>模型评审失败：{html.escape(case['judge_error'])}</p>"
    (root / "scene-report.html").write_text(body)
