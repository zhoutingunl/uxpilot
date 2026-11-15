from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from playwright.async_api import async_playwright

from .audio_recording import BrowserAudioRecorder
from .evidence import Evidence
from .media import encode_video, probe_media, relative_video_events, review_video
from .policy import Policy, safe_url, scrub
from .schema import Action, App, Assertion, Browser, Task
from .verifier import check_events

OBSERVE = r"""() => {
 const rect=e=>{const b=e.getBoundingClientRect();return {x:Math.round(b.x),y:Math.round(b.y),w:Math.round(b.width),h:Math.round(b.height)}};
 const shown=e=>{const s=getComputedStyle(e),b=e.getBoundingClientRect();return s.display!=='none'&&s.visibility!=='hidden'&&b.width>0&&b.height>0};
 let counter=0;const nodes=[];
 for(const e of document.querySelectorAll('button,a[href],input:not([type=hidden]),select,textarea,summary,[role=button]')){
  if(!shown(e)||nodes.length>=100)continue;
  if(e.closest('#activation')||e.type==='password')continue;
  const id='n'+(++counter);e.setAttribute('data-ux-id',id);
  const b=rect(e);const label=e.id?document.querySelector('label[for="'+CSS.escape(e.id)+'"]'):null;
  nodes.push({id,tag:e.tagName.toLowerCase(),dom_id:e.id,name:(e.getAttribute('aria-label')||label?.innerText||e.innerText||e.placeholder||e.type||'').trim().slice(0,200),
   value:['TEXTAREA','INPUT','SELECT'].includes(e.tagName)&&!['password','hidden'].includes(e.type)?e.value.slice(0,2500):undefined,disabled:!!e.disabled,href:e.tagName==='A'?e.href:null,rect:b,in_view:b.y>=0&&b.y+b.h<=innerHeight,
   options:e.tagName==='SELECT'?[...e.options].map(o=>({value:o.value,label:o.text,disabled:o.disabled})):undefined});
 }
 const copy=document.body.cloneNode(true);const originals=[...document.body.querySelectorAll('*')],clones=[...copy.querySelectorAll('*')];for(let i=0;i<originals.length;i++){if(!shown(originals[i]))clones[i].remove();}for(const e of copy.querySelectorAll('script,style,input,textarea,#activation'))e.remove();
 return {url:location.href,title:document.title,language:document.documentElement.lang,text:(copy.textContent||'').replace(/\s+/g,' ').slice(0,16000),
   nodes,viewport:{width:innerWidth,height:innerHeight,scroll_y:scrollY,scroll_width:document.documentElement.scrollWidth},
   statuses:[...document.querySelectorAll('[role=status],[role=alert]')].filter(shown).map(e=>e.textContent.trim()).slice(0,20)};
}"""


class BrowserRuntime:
    """One isolated context per attempt; no model-supplied JS, shell, or selectors."""

    def __init__(self, app: App, config: Browser, task: Task, evidence: Evidence):
        self.app, self.config, self.task, self.evidence = app, config, task, evidence
        self.policy = Policy(app)
        self.playwright = self.browser = self.context = self.page = None
        self.touch_session = None
        self.nodes = {}
        self.observed_links: set[str] = set()
        self.visited_urls: set[str] = set()
        self.owned_room = None
        self.action_ids = set()
        self.initial_states_seen: set[str] = set()
        self.pending: set[asyncio.Task] = set()
        self.video_info = {"status": "disabled"}
        self.audio_recorder = None
        self.response_audio_frames = 0
        self.response_audio_bytes = 0
        self.response_audio_rate = None
        self.response_audio_rates = set()
        self.response_turn_start = None
        self.response_turn_rates = set()
        self.audio_signal_seen = False
        self.last_mark = None
        self.cleanup = {"status": "not_applicable" if app.adapter == "generic" else "not_started"}

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)
        return task

    async def start(self):
        self.playwright = await async_playwright().start()
        if self.config.engine == "webkit" and self.task.audio:
            raise ValueError(
                "WebKit microphone fixtures are not supported; use Chromium or a physical-device audio test"
            )
        args = []
        if self.task.audio:
            args = [
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={self.task.audio.path}",
            ]
        options = {"headless": self.config.headless, "args": args}
        executable = (
            (os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or self.config.executable_path)
            if self.config.engine == "chromium"
            else None
        )
        if executable:
            options["executable_path"] = executable
        elif self.config.channel and self.config.engine == "chromium":
            options["channel"] = self.config.channel
        self.browser = await getattr(self.playwright, self.config.engine).launch(**options)
        kwargs = {
            "viewport": {"width": self.config.width, "height": self.config.height},
            "locale": self.config.locale,
            "service_workers": "block",
            "permissions": ["microphone"] if self.task.audio else [],
        }
        if self.config.device != "desktop":
            from .devices import PROFILES

            preset = dict(self.playwright.devices[PROFILES[self.config.device]["preset"]])
            preset.pop("default_browser_type", None)
            kwargs = {**preset, **kwargs}
        if self.config.video:
            kwargs.update(
                record_video_dir=str(self.evidence.root / "raw-video"),
                record_video_size={"width": self.config.width, "height": self.config.height},
            )
        self.context = await self.browser.new_context(**kwargs)
        if self.config.video and self.config.record_audio and self.app.adapter == "duckking":
            self.audio_recorder = BrowserAudioRecorder(
                self.evidence,
                input_enabled=bool(self.task.audio),
                max_seconds=self.config.video_max_seconds,
                max_bytes=self.config.audio_max_bytes,
            )
            await self.audio_recorder.install(self.context)
        await self.context.route("**/*", self._route)
        await self.context.route_web_socket("**/*", self._ws_route)
        selectors = ",".join(self.config.redact_selectors)
        await self.context.add_init_script(
            script="""(selectors => {
          const install=()=>{if(!document.documentElement)return; if(document.getElementById('__ux_mask'))return;
            const style=document.createElement('style');style.id='__ux_mask';style.textContent=selectors+'{visibility:hidden!important}';document.documentElement.append(style);};
          new MutationObserver(install).observe(document,{childList:true,subtree:true});install();
          if(navigator.mediaDevices?.getUserMedia){const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
            navigator.mediaDevices.getUserMedia=async c=>{const stream=await original(c);window.__uxMicAt=performance.now();return stream;};}
        })("""
            + json.dumps(selectors)
            + ")"
        )
        if self.app.adapter == "duckking":
            await self.context.expose_binding("__uxMark", self._mark)
            await self.context.expose_binding(
                "__uxDemoMark",
                lambda source, data: self.evidence.event(
                    "demo_result", data={k: data.get(k) for k in ("scene", "mode", "status")}
                ),
            )
            await self.context.add_init_script(
                script="""(()=>{let previous='';const sample=()=>{const node=document.querySelector('#theatre-result');if(!node||node.textContent===previous)return;previous=node.textContent;try{const proof=JSON.parse(previous);window.__uxDemoMark({scene:proof.scene,mode:proof.mode,status:proof.status}).catch(()=>{})}catch{}};new MutationObserver(sample).observe(document,{childList:true,subtree:true,characterData:true});})()"""
            )
            await self.context.add_init_script(
                script="""(() => {
          let previous='';setInterval(()=>{const e=document.querySelector('#viewport'),p=document.querySelector('#physics-viewport');
           const data={model:e?.dataset.model,home_timing:e?.dataset.modelTiming,scene_timing:p?.dataset.modelTiming,
             scene_placeholder_hidden:document.querySelector('#scene-placeholder')?.hidden};const key=JSON.stringify(data);
           if(key!==previous){previous=key;window.__uxMark(data).catch(()=>{});}},150);
        })()"""
            )
        self.video_start_ms = self.evidence.ms()
        if self.audio_recorder:
            self.audio_recorder.start_offset_ms = self.video_start_ms
        self.page = await self.context.new_page()
        self.touch_session = (
            await self.context.new_cdp_session(self.page)
            if self.config.device != "desktop" and self.config.engine == "chromium"
            else None
        )
        self.environment = {
            "device": self.config.device,
            "engine": self.config.engine,
            "viewport": kwargs["viewport"],
            "has_touch": kwargs.get("has_touch", False),
            "is_mobile": kwargs.get("is_mobile", False),
            "device_scale_factor": kwargs.get("device_scale_factor", 1),
            "user_agent": await self.page.evaluate("navigator.userAgent"),
            "navigator_max_touch_points": await self.page.evaluate("navigator.maxTouchPoints"),
            "scroll_input": "touch"
            if self.touch_session
            else "programmatic"
            if self.config.device != "desktop"
            else "wheel",
            "physical_device": False,
            "limitations": (
                ["浏览器设备模拟，未进行真机验证；系统软键盘、动态地址栏、安全区和硬件性能仍需真机复核。"]
                + (
                    ["WebKit 滚动采用程序滚动，未验证真实滑动手势。"]
                    if self.config.engine == "webkit"
                    else []
                )
            )
            if self.config.device != "desktop"
            else [],
        }
        if self.config.device != "desktop" and not self.environment["navigator_max_touch_points"]:
            self.environment["limitations"].append(
                "此模拟引擎 navigator.maxTouchPoints 为 0；触屏点击事件已启用，该能力属性与真机可能不同。"
            )
        self.evidence.write("browser-environment.json", self.environment)
        if self.audio_recorder:
            self.audio_recorder.page = self.page
        self.video_clock_uncertainty_ms = self.evidence.ms() - self.video_start_ms
        self.page.set_default_timeout(self.config.action_timeout_ms)
        self.page.on(
            "console",
            lambda m: (
                self.evidence.event("console", level=m.type, text=m.text[:2000])
                if m.type in {"warning", "error"}
                else None
            ),
        )
        self.page.on("pageerror", lambda e: self.evidence.event("pageerror", error=str(e)[:2000]))
        self.page.on(
            "requestfailed",
            lambda r: self.evidence.event("request_failed", url=safe_url(r.url), failure=r.failure),
        )
        self.page.on(
            "response",
            lambda r: (
                self.evidence.event("http_error", url=safe_url(r.url), status=r.status)
                if r.status >= 400
                else None
            ),
        )
        self.page.on("websocket", self._websocket)
        self.page.on("dialog", lambda dialog: self.spawn(dialog.dismiss()))
        self.context.on("page", lambda p: self.spawn(self._popup(p)) if p != self.page else None)
        self.evidence.event("browser_started", version=self.browser.version, viewport=kwargs["viewport"])
        url = self.policy.check_url(self.task.start_path)
        if self.app.adapter == "duckking":
            parsed = urlsplit(url)
            query = dict(parse_qsl(parsed.query))
            query["measurement"] = "verification"
            url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
        self.evidence.event("navigation", url=safe_url(url))
        await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if self.app.adapter == "duckking":
            await self.page.wait_for_function(
                """() => {const room=new URL(location.href).searchParams.get('room');
                return /^[0-9]+$/.test(room||'')&&!!document.querySelector('.room-links a[data-room="'+room+'"][aria-current=page]');}""",
                timeout=20000,
            )
            self.owned_room = parse_qs(urlsplit(self.page.url).query).get("room", [None])[0]
            self.policy.room = self.owned_room
            self.evidence.event("room_allocated", room=self.owned_room)
        await self.page.wait_for_timeout(400)

    async def _route(self, route):
        try:
            self.policy.check_request(
                route.request.url, route.request.is_navigation_request(), route.request.method
            )
            if (
                self.owned_room
                and route.request.method == "POST"
                and urlsplit(route.request.url).path.rstrip("/") in {"/api/rooms", "/api/rooms/claim"}
            ):
                raise ValueError("cannot allocate additional rooms from one run")
        except ValueError:
            self.evidence.event("policy_block", url=safe_url(route.request.url))
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    async def _ws_route(self, route):
        try:
            self.policy.check_url(route.url)
        except ValueError:
            self.evidence.event("policy_block", url=safe_url(route.url))
            await route.close(code=1008, reason="outside evaluation scope")
            return
        route.connect_to_server()

    async def _popup(self, page):
        self.evidence.event("popup_closed", url=safe_url(page.url))
        await page.close()

    def _mark(self, source, data):
        self.evidence.event("display_state", data=data)

    def _websocket(self, socket):
        endpoint = urlsplit(socket.url).path
        socket.on("framesent", lambda data: self._frame(data, "sent", endpoint))
        socket.on("framereceived", lambda data: self._frame(data, "received", endpoint))
        socket.on("socketerror", lambda e: self.evidence.event("websocket_error", error=str(e)[:500]))

    def _frame(self, raw, direction, endpoint=""):
        if self.app.adapter != "duckking":
            # Generic sites have arbitrary payloads; capture metadata without guessing their protocol.
            self.evidence.event(
                "websocket_frame",
                direction=direction,
                endpoint=endpoint,
                bytes=len(raw),
                binary=not isinstance(raw, str),
            )
            return
        if not isinstance(raw, str):
            return
        try:
            data = json.loads(raw)
        except ValueError:
            return
        if not isinstance(data, dict):
            return
        kind = data.get("type")
        if kind == "audio":
            import base64

            if direction != "received":
                return
            try:
                decoded = base64.b64decode(data.get("pcm", ""), validate=True)
            except (ValueError, TypeError):
                return
            rate = data.get("sample_rate")
            if not decoded or len(decoded) % 2 or type(rate) is not int or not 8000 <= rate <= 192000:
                return
            self.response_audio_frames += 1
            self.response_audio_bytes += len(decoded)
            self.response_audio_rate = rate
            self.response_audio_rates.add(rate)
            self.response_turn_rates.add(rate)
            if self.response_audio_bytes <= 8_000_000:
                # Public/synthetic task responses only. Raw audio is never sent as a prompt by default.
                with (self.evidence.root / "response.pcm").open("ab") as f:
                    f.write(decoded)
            if not self.audio_signal_seen and any(decoded):
                self.audio_signal_seen = True
                self.evidence.event("first_audio", sample_rate=rate)
            return
        if kind == "state":
            baseline = endpoint not in self.initial_states_seen
            self.initial_states_seen.add(endpoint)
            for event in data.get("events", []):
                key = (event.get("id"), event.get("action_id"))
                if key in self.action_ids:
                    continue
                self.action_ids.add(key)
                if not baseline:
                    self.evidence.event("product_action", data=event)
            return
        if kind in {
            "voice_status",
            "voice_route",
            "transcript",
            "playback_end",
            "voice_milestone",
            "playback_status",
            "error",
            "scene_error",
            "connect",
            "listen_start",
            "listen_stop",
            "interrupt",
            "disconnect",
            "reset",
        }:
            # Whitelist relevant values; no raw protocol session/device identity.
            fields = {
                k: data[k]
                for k in (
                    "type",
                    "state",
                    "mode",
                    "route",
                    "role",
                    "source",
                    "text",
                    "generation",
                    "reason",
                    "error",
                    "message",
                    "feedback",
                    "aborted",
                    "stage",
                    "language",
                    "phase",
                    "result",
                    "output_state",
                    "received_frames",
                    "scheduled_frames",
                    "ended_frames",
                    "dropped_frames",
                    "queued_ms",
                    "signal_peak_milli",
                )
                if k in data
            }
            self.evidence.event("voice_event", direction=direction, data=fields)

    async def observe(self, step: int):
        # Clear stale handles before constructing the current observation.
        await self.page.locator("[data-ux-id]").evaluate_all(
            "es=>es.forEach(e=>e.removeAttribute('data-ux-id'))"
        )
        result = scrub(await self.page.evaluate(OBSERVE))
        result["frames"] = []
        for frame in self.page.frames:
            if frame == self.page.main_frame:
                continue
            try:
                self.policy.check_url(frame.url)
                text = await frame.locator("body").inner_text(timeout=1500)
                result["frames"].append({"url": safe_url(frame.url), "text": scrub(text[:4000])})
            except Exception:
                continue
        self.nodes = {n["id"]: n for n in result["nodes"]}
        self.observed_links = {n["href"] for n in result["nodes"] if n.get("href")}
        for node in result["nodes"]:
            if node.get("href"):
                node["href"] = safe_url(node["href"])
        result["url"] = safe_url(result["url"])
        self.visited_urls.add(result["url"])
        result["step"] = step
        result["t_ms"] = self.evidence.ms()
        result["screenshot"] = f"screenshots/step-{step:03}.png"
        result["evidence_ref"] = f"observations/step-{step:03}.json"
        await self.page.screenshot(
            path=str(self.evidence.root / result["screenshot"]), animations="disabled", timeout=10000
        )
        self.evidence.event(
            "observation",
            step=step,
            url=result["url"],
            screenshot=result["screenshot"],
            evidence_ref=result["evidence_ref"],
            visible_controls=len(result["nodes"]),
        )
        self.evidence.observations.append(result)
        self.evidence.write(result["evidence_ref"], result)
        return result

    async def perform(self, action: Action):
        began = self.evidence.ms()
        self.evidence.event("agent_action_start", action=action.model_dump())
        try:
            node = self.nodes.get(action.target) if action.target else None
            if action.target and not node:
                raise ValueError("target is not present in the latest observation")
            if node:
                self.policy.check_control(node)
                if node.get("disabled") and action.kind in {"click", "fill", "select", "speak"}:
                    raise ValueError(
                        "observed control is disabled; wait or use an enabled control to change state"
                    )
            locator = self.page.locator(f'[data-ux-id="{action.target}"]') if node else None
            if action.kind == "click":
                # Follow an observed same-origin link in the owned tab; no orphan tabs/worlds.
                if node.get("href"):
                    self.policy.check_url(node["href"])
                    await locator.evaluate("e=>{if(e.target==='_blank')e.target='_self'}")
                if self.config.device != "desktop":
                    await locator.tap()
                else:
                    await locator.click()
            elif action.kind == "fill":
                await locator.fill(action.text)
            elif action.kind == "select":
                await locator.select_option(action.text)
            elif action.kind == "press":
                if locator:
                    await locator.focus()
                if action.key in {"Enter", "Space"}:
                    focused = await self.page.evaluate(
                        """()=>{const e=document.activeElement;return {name:e.getAttribute('aria-label')||e.innerText||'',dom_id:e.id,href:e.tagName==='A'?e.href:null}}"""
                    )
                    self.policy.check_control(focused)
                await self.page.keyboard.press(action.key)
            elif action.kind == "scroll":
                if self.config.device != "desktop" and self.touch_session:
                    size = self.page.viewport_size
                    x, start = (
                        size["width"] / 2,
                        size["height"] * (0.8 if action.direction == "down" else 0.2),
                    )
                    delta = min(action.pixels, size["height"] * 0.6) * (
                        -1 if action.direction == "down" else 1
                    )
                    await self.touch_session.send(
                        "Input.dispatchTouchEvent",
                        {"type": "touchStart", "touchPoints": [{"x": x, "y": start}]},
                    )
                    try:
                        for i in range(1, 7):
                            await self.touch_session.send(
                                "Input.dispatchTouchEvent",
                                {"type": "touchMove", "touchPoints": [{"x": x, "y": start + delta * i / 6}]},
                            )
                            await self.page.wait_for_timeout(25)
                    finally:
                        await self.touch_session.send(
                            "Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []}
                        )
                elif self.config.device != "desktop":
                    await self.page.evaluate(
                        "pixels=>scrollBy(0,pixels)",
                        action.pixels * (1 if action.direction == "down" else -1),
                    )
                else:
                    await self.page.mouse.wheel(0, action.pixels * (1 if action.direction == "down" else -1))
            elif action.kind == "wait":
                await self.page.wait_for_timeout(action.seconds * 1000)
            elif action.kind == "speak":
                if not self.task.audio:
                    raise ValueError("task has no authorized audio fixture")
                if self.app.adapter == "duckking" and node.get("dom_id") != "talk-button":
                    raise ValueError("speak must target the observed push-to-talk button")
                if not await locator.is_enabled():
                    raise ValueError("microphone/voice is not ready")
                await locator.scroll_into_view_if_needed()
                audio = self.task.audio
                delay = await self.page.evaluate(
                    """a=>{if(!Number.isFinite(window.__uxMicAt))throw Error('microphone was not acquired');
                 const phase=(performance.now()-window.__uxMicAt)%a.loop_ms;
                 return phase<a.start_ms-100?a.start_ms-phase:a.loop_ms+a.start_ms-phase;}""",
                    audio.model_dump(),
                )
                await self.page.wait_for_timeout(delay)
                box = await locator.bounding_box()
                self.audio_signal_seen = False
                self.response_turn_start = self.response_audio_bytes
                self.response_turn_rates.clear()
                x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                if not self.touch_session:
                    await self.page.mouse.move(x, y)
                self.evidence.event(
                    "audio_input_start", transcript=audio.transcript, input_kind=audio.input_kind
                )
                if self.audio_recorder:
                    await self.audio_recorder.begin_input()
                try:
                    if self.touch_session:
                        await self.touch_session.send(
                            "Input.dispatchTouchEvent",
                            {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]},
                        )
                    else:
                        await self.page.mouse.down()
                    await self.page.wait_for_timeout(audio.hold_ms)
                finally:
                    try:
                        if self.touch_session:
                            await self.touch_session.send(
                                "Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []}
                            )
                        else:
                            await self.page.mouse.up()
                    finally:
                        if self.audio_recorder:
                            await self.audio_recorder.end_input()
                        self.evidence.event("audio_input_end")
            elif action.kind == "reload":
                await self.page.reload(wait_until="domcontentloaded", timeout=30000)
            elif action.kind == "back":
                if await self.page.evaluate("history.length") <= 1:
                    raise ValueError("no observed previous page to return to")
                await self.page.go_back(wait_until="domcontentloaded", timeout=30000)
            elif action.kind == "rotate":
                if self.config.device == "desktop":
                    raise ValueError("rotation requires a mobile profile")
                size = self.page.viewport_size
                await self.page.set_viewport_size({"width": size["height"], "height": size["width"]})
                self.evidence.event(
                    "viewport_rotated", viewport=self.page.viewport_size, physical_rotation=False
                )
            elif action.kind == "navigate":
                url = self.policy.check_url(action.url)
                if url not in self.observed_links and url not in self.visited_urls:
                    raise ValueError("navigate URL must be an observed link or a page visited in this run")
                await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await self.page.wait_for_timeout(250)
            return {"ok": True, "duration_ms": self.evidence.ms() - began}
        except Exception as e:
            return {
                "ok": False,
                "error": str(scrub(str(e)))[:1000],
                "duration_ms": self.evidence.ms() - began,
            }
        finally:
            self.evidence.event("agent_action_end", duration_ms=self.evidence.ms() - began)

    async def inspect_assertion(self, assertion):
        # Called by verifier only, never exposed in Explorer's observations.
        if assertion.kind == "page_loaded":
            return await self.page.evaluate(
                "()=>['interactive','complete'].includes(document.readyState)&&!!document.body&&document.body.innerText.trim().length>0"
            )
        if assertion.kind == "no_page_errors":
            return not any(e["kind"] == "pageerror" for e in self.evidence.events)
        if assertion.kind == "no_horizontal_overflow":
            return await self.page.evaluate("()=>document.documentElement.scrollWidth<=innerWidth+1")
        if assertion.kind == "mobile_viewport":
            return await self.page.evaluate(
                "expected=>Math.abs(innerWidth-expected)<=2", self.page.viewport_size["width"]
            )
        if assertion.kind == "element_visible":
            return await self.page.locator(assertion.selector).is_visible()
        if assertion.kind == "url_contains":
            return assertion.contains in self.page.url
        if assertion.kind == "page_language":
            language = (await self.page.locator("html").get_attribute("lang") or "").lower()
            expected = assertion.contains.lower()
            return language == expected or language.startswith(expected + "-")
        if assertion.kind == "text":
            locator = self.page.locator(assertion.selector)
            return await locator.is_visible() and assertion.contains in (await locator.inner_text())
        if assertion.kind == "text_absent":
            locator = self.page.locator(assertion.selector)
            return await locator.is_visible() and assertion.contains not in (await locator.inner_text())
        if assertion.kind == "demo_completed":
            if self.app.adapter != "duckking":
                raise ValueError("demo_completed requires the duckking adapter")
            return await self.page.evaluate(
                """() => {try{const data=JSON.parse(document.querySelector('#theatre-result')?.textContent||'null');return data?.status==='completed'&&data?.mode==='scripted_rehearsal'}catch{return false}}"""
            )
        if assertion.kind == "element_unobscured":
            return await self.page.locator(assertion.selector).evaluate(
                """e=>{const r=e.getBoundingClientRect();const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return r.width>0&&r.height>0&&!!hit&&(hit===e||e.contains(hit))}"""
            )
        if assertion.kind == "focused_input_visible":
            return await self.page.evaluate(
                """()=>{const e=document.activeElement;if(!e||!e.matches('input,textarea,[contenteditable=true]'))return true;const r=e.getBoundingClientRect(),v=visualViewport;const left=v?.offsetLeft??0,top=v?.offsetTop??0;const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return r.top>=top&&r.bottom<=top+(v?.height??innerHeight)&&r.left>=left&&r.right<=left+(v?.width??innerWidth)&&!!hit&&(hit===e||e.contains(hit))}"""
            )
        if assertion.kind == "model_ready":
            if self.app.adapter != "duckking":
                raise ValueError("model_ready requires the duckking adapter")
            return await self.page.evaluate("""()=>document.querySelector('#viewport')?.dataset.model==='official'||
              (JSON.parse(document.querySelector('#physics-viewport')?.dataset.modelTiming||'{}').result==='ready'&&document.querySelector('#scene-placeholder')?.hidden===true)""")
        if assertion.kind == "viewport_overlap":
            return await self.page.evaluate(
                """([a,b])=>[a,b].every(s=>{const e=document.querySelector(s);if(!e)return false;
              const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth})""",
                [assertion.selector, assertion.other_selector],
            )
        return None

    def export_response_audio(self):
        """Snapshot only the last spoken turn, excluding prior replies/greetings."""
        import wave

        pcm = self.evidence.root / "response.pcm"
        if (
            self.response_turn_start is None
            or len(self.response_turn_rates) != 1
            or not pcm.is_file()
            or pcm.stat().st_size != self.response_audio_bytes
            or self.response_audio_bytes <= self.response_turn_start
        ):
            return None
        with pcm.open("rb") as source:
            source.seek(self.response_turn_start)
            raw = source.read()
        output = self.evidence.root / "response.wav"
        with wave.open(str(output), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(next(iter(self.response_turn_rates)))
            wav.writeframes(raw)
        return output

    async def finish(self):
        raw_video = None
        cleanup_cookies = httpx.Cookies()
        if self.owned_room and self.context:
            # Retain this isolated visitor's authorization only in memory while
            # closing the browser releases its voice/viewer connections.
            try:
                for cookie in await self.context.cookies([self.app.base_url]):
                    cleanup_cookies.set(
                        cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie["path"]
                    )
            except Exception:
                self.evidence.event("cleanup_auth_unavailable")
        if self.page and not self.page.is_closed():
            try:
                resources = await self.page.evaluate("""()=>performance.getEntriesByType('resource').map(e=>({url:e.name,
                  duration_ms:e.duration,transfer_bytes:e.transferSize,encoded_bytes:e.encodedBodySize})).slice(-200)""")
                for r in resources:
                    r["url"] = safe_url(r["url"])
                self.evidence.write("resources.json", resources)
            except Exception:
                pass
            if self.config.video:
                raw_video = self.page.video
        if self.audio_recorder:
            try:
                await self.audio_recorder.stop()
            except Exception:
                # A recording/storage failure must never prevent room/browser cleanup.
                self.audio_recorder.warnings.add("audio_capture_stop_failed")
        # Closing the page releases voice and viewer ownership before conditional scene cleanup.
        try:
            if self.context:
                await self.context.close()
            if self.pending:
                await asyncio.gather(*list(self.pending), return_exceptions=True)
        finally:
            try:
                if self.browser:
                    await self.browser.close()
            finally:
                if self.playwright:
                    await self.playwright.stop()
        if self.owned_room:
            self.cleanup = {"status": "failed"}
            try:
                origin = urlsplit(self.app.base_url)
                async with httpx.AsyncClient(
                    timeout=12,
                    cookies=cleanup_cookies,
                    headers={"Origin": f"{origin.scheme}://{origin.netloc}"},
                ) as client:
                    rooms = [self.owned_room, *sorted(self.policy.additional_rooms)]
                    results = {}
                    for room in rooms:
                        for attempt in range(3):
                            url = (
                                self.app.base_url.rstrip("/")
                                + "/api/scenes/close?if_unused=true&room="
                                + room
                            )
                            response = await client.post(url)
                            if response.status_code in {204, 404}:
                                results[room] = {"status": "released", "http_status": response.status_code}
                                break
                            if response.status_code != 409:
                                results[room] = {"status": "failed", "http_status": response.status_code}
                                break
                            results[room] = {"status": "retained_busy_room", "http_status": 409}
                            if attempt < 2:
                                await asyncio.sleep(0.4)
                    self.cleanup = results[self.owned_room]
                    if len(rooms) > 1:
                        self.cleanup = {
                            "status": "released"
                            if all(r["status"] == "released" for r in results.values())
                            else "incomplete",
                            "rooms": results,
                        }
            except (httpx.HTTPError, ValueError):
                self.cleanup = {"status": "failed"}
        self.evidence.event("cleanup", **self.cleanup)
        if raw_video:
            try:
                source = Path(await raw_video.path())
                mixed_audio = None
                if self.audio_recorder:
                    duration = (await probe_media(source))["duration_seconds"]
                    mixed_audio = await self.audio_recorder.mix(self.video_start_ms, duration)
                try:
                    info = await encode_video(
                        source,
                        self.evidence.root / "session.mp4",
                        self.config.video_max_seconds,
                        self.config.video_max_bytes,
                        audio_source=mixed_audio,
                    )
                finally:
                    if mixed_audio:
                        mixed_audio.unlink(missing_ok=True)
                self.video_info = {
                    "status": "ready",
                    **info,
                    "video_start_event_ms": self.video_start_ms,
                    "clock_uncertainty_ms": self.video_clock_uncertainty_ms,
                    "audio_capture": self.audio_recorder.metadata() if self.audio_recorder else None,
                }
                review = await review_video(
                    self.evidence.root / "session.mp4",
                    self.evidence.root / "review.mp4",
                    relative_video_events(self.evidence.events, self.video_start_ms),
                    info["duration_seconds"],
                    self.config.video_max_bytes,
                )
                self.video_info["review"] = review
                self.evidence.write("video-timeline.json", review)
            except Exception as e:
                if self.video_info.get("status") == "ready":
                    self.video_info["review_error"] = str(e)
                else:
                    self.video_info = {"status": "failed", "reason": str(e)}
        self.evidence.write("video.json", self.video_info)
        self.evidence.write(
            "audio.json",
            {
                "frames": self.response_audio_frames,
                "bytes": self.response_audio_bytes,
                "sample_rate": self.response_audio_rate,
                "sample_rates": sorted(self.response_audio_rates),
                "browser_playback_completed": check_events(
                    Assertion(id="audio-played", kind="audio_played"), self.evidence.events
                ),
                "speech_content_independently_verified": bool(
                    checks := [a for a in self.task.assertions if a.kind == "response_speech"]
                )
                and all(check_events(a, self.evidence.events) for a in checks),
                "speech_content_verification_scope": "configured_keywords_only",
                "playback_verified_by_human": False,
                "recording": self.audio_recorder.metadata() if self.audio_recorder else None,
            },
        )
