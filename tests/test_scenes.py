import json
from types import SimpleNamespace

import httpx
import pytest
from test_browser_integration import local_site
from test_llm import providers

from ai_ux import scenes
from ai_ux.evidence import Evidence
from ai_ux.schema import App, Browser, Config, Task


def fixture(bug):
    return """<!doctype html><html><body>
    <div class="room-links"><a data-room="7" aria-current="page">Room 7</a></div>
    <button data-scene="ball">Ball</button><button id="release-scene" disabled>Release</button>
    <div id="physics-viewport"><div id="scene-placeholder">选择场景开始</div><canvas></canvas></div>
    <span id="scene-title">等待启动</span><span id="sim-status">选择场景开始</span>
    <select id="demo-mode"><option value="rehearsal">Local</option><option value="live">Cloud</option></select>
    <button data-demo="duel">Duet</button><div class="stage-worlds"><div id="demo-viewport"></div><div id="demo-second"></div></div>
    <script>
    const bug=BUG,url=new URL(location.href);url.searchParams.set('room','7');history.replaceState(null,'',url);
    const $=id=>document.getElementById(id),release=$('release-scene'),placeholder=$('scene-placeholder');
    document.querySelector('[data-scene]').onclick=()=>{
      $('physics-viewport').dataset.modelTiming=JSON.stringify({result:'ready'});
      placeholder.innerHTML='<span>正在绘制第一帧 已等待 6 秒</span><progress></progress><button>停止等待</button>';
      placeholder.hidden=true;$('scene-title').textContent='单鸭 + 球';release.disabled=false;
    };
    release.onclick=async()=>{await fetch('/api/scenes/close',{method:'POST'});
      $('scene-title').textContent='等待启动';release.disabled=true;placeholder.hidden=false;
      if(!bug)placeholder.textContent='选择场景开始';
    };
    document.querySelector('[data-demo]').onclick=()=>{
      for(const id of ['demo-viewport','demo-second'])$(id).append(document.createElement('canvas'));
      setTimeout(()=>{const proof=document.createElement('script');proof.type='application/json';proof.id='theatre-result';
        proof.textContent=JSON.stringify({scene:'duel',status:'completed',mode:'scripted_rehearsal'});document.body.append(proof);
        if(bug)setTimeout(()=>$('demo-second').replaceChildren(),120);
      },800);
    };
    </script></body></html>""".replace("BUG", json.dumps(bug)).encode()


@pytest.mark.parametrize("bug", [True, False])
async def test_lifecycle_catches_reported_failures_and_accepts_healthy_pages(tmp_path, monkeypatch, bug):
    async with local_site(fixture(bug)) as url:
        config = Config(
            app=App(name="Duck", base_url=url, allowed_hosts=["127.0.0.1"], adapter="duckking"),
            models=providers(monkeypatch),
            browser=Browser(video=False),
            output_dir=str(tmp_path),
            tasks=[Task(id="test", goal="Scene check")],
        )
        root, report = await scenes.check_scenes(config, timeout=3, hold_seconds=0.6)
        assert report["status"] == ("failed" if bug else "passed")
        release, duel = report["cases"]
        assert release["checks"]["release_request_succeeded"]
        assert release["checks"]["idle_without_stale_loading"] is not bug
        assert release["checks"]["can_restart"]
        assert duel["checks"]["both_present_during_performance"]
        assert duel["checks"]["scripted_performance_completed"]
        assert duel["checks"]["both_retained_after_completion"] is not bug
        assert (root / "scene-report.html").is_file()
        assert all(
            (root / c["id"] / p["screenshot"]).is_file() for c in report["cases"] for p in c["checkpoints"]
        )


async def test_scene_judge_never_uses_minimax_even_when_primary_fails(tmp_path, monkeypatch):
    requests = []

    def request(req):
        requests.append((req.url.host, json.loads(req.content)["model"]))
        return httpx.Response(429)

    original = scenes.LLM
    clients = []

    def llm(config, **kwargs):
        client = httpx.AsyncClient(transport=httpx.MockTransport(request))
        clients.append(client)
        return original(config, client=client, **kwargs)

    monkeypatch.setattr(scenes, "LLM", llm)
    async with local_site(fixture(False)) as url:
        config = Config(
            app=App(name="Duck", base_url=url, allowed_hosts=["127.0.0.1"], adapter="duckking"),
            models=providers(monkeypatch),
            browser=Browser(video=False),
            output_dir=str(tmp_path),
            tasks=[Task(id="test", goal="Scene check")],
        )
        _, report = await scenes.check_scenes(
            config, cases=["release"], judge=True, timeout=3, hold_seconds=0.1
        )
    for client in clients:
        await client.aclose()
    assert requests and all(r == ("primary.test", "glm-5.3-Flash") for r in requests)
    assert report["status"] == "passed"  # Deterministic checks and Judge availability stay separate.
    assert report["judge_status"] == "failed"
    assert report["cases"][0]["judge_status"] == "failed"


class Route:
    def __init__(self, url, room="8"):
        self.request = SimpleNamespace(url=url, method="POST", is_navigation_request=lambda: False)
        self.room = room
        self.aborted = self.fulfilled = self.continued = False

    async def fetch(self, **kwargs):
        async def data():
            return {"room": self.room}

        return SimpleNamespace(ok=True, json=data)

    async def fulfill(self, **kwargs):
        self.fulfilled = True

    async def abort(self, *args):
        self.aborted = True

    async def continue_(self):
        self.continued = True


async def test_secondary_allocation_is_scoped_bounded_and_default_policy_stays_single_room(tmp_path):
    app = App(name="Duck", base_url="https://duck.test", allowed_hosts=["duck.test"], adapter="duckking")
    runtime = scenes.SceneRuntime(
        app, Browser(video=False), Task(id="test", goal="test"), Evidence(tmp_path / "duet"), duet=True
    )
    runtime.owned_room = runtime.policy.room = "7"
    external = Route("https://outside.test/api/rooms/claim")
    await runtime._route(external)
    assert external.aborted and not runtime.policy.additional_rooms
    claim = Route("https://duck.test/api/rooms/claim?room=7")
    await runtime._route(claim)
    assert claim.fulfilled and runtime.policy.additional_rooms == {"8"}
    assert runtime.policy.check_url("/api/scenes/state?room=8")
    with pytest.raises(ValueError):
        runtime.policy.check_url("/api/scenes/state?room=9")
    third = Route("https://duck.test/api/rooms/claim?room=7", "9")
    await runtime._route(third)
    assert third.aborted and runtime.policy.additional_rooms == {"8"}
    ordinary = scenes.SceneRuntime(
        app, Browser(video=False), Task(id="test", goal="test"), Evidence(tmp_path / "ordinary")
    )
    ordinary.owned_room = ordinary.policy.room = "7"
    blocked = Route("https://duck.test/api/rooms/claim?room=7")
    await ordinary._route(blocked)
    assert blocked.aborted
    voice = Route("https://duck.test/api/voice/connect?room=7")
    await runtime._route(voice)
    assert voice.aborted


async def test_missing_scene_controls_are_blocked_not_passed(tmp_path, monkeypatch):
    async with local_site(fixture(False).replace(b'data-scene="ball"', b'data-scene="other"')) as url:
        config = Config(
            app=App(name="Duck", base_url=url, allowed_hosts=["127.0.0.1"], adapter="duckking"),
            models=providers(monkeypatch),
            browser=Browser(video=False, action_timeout_ms=1000),
            output_dir=str(tmp_path),
            tasks=[Task(id="test", goal="Scene check")],
        )
        _, report = await scenes.check_scenes(config, cases=["release"], timeout=1)
    assert report["status"] == "blocked"
    assert report["cases"][0]["error"]


async def test_cleanup_only_releases_allocated_rooms_and_retains_busy_rooms(tmp_path, monkeypatch):
    requested = []

    def request(req):
        requested.append(req)
        return httpx.Response(204 if req.url.params["room"] == "7" else 409)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "ai_ux.browser.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(request), **kwargs),
    )
    app = App(name="Duck", base_url="https://duck.test", allowed_hosts=["duck.test"], adapter="duckking")
    runtime = scenes.SceneRuntime(
        app, Browser(video=False), Task(id="test", goal="test"), Evidence(tmp_path / "cleanup"), duet=True
    )
    runtime.owned_room = runtime.policy.room = "7"
    runtime.policy.additional_rooms.add("8")
    await runtime.finish()
    assert [r.url.params["room"] for r in requested] == ["7", "8", "8", "8"]
    assert all(
        r.url.params["if_unused"] == "true" and r.headers["Origin"] == "https://duck.test" for r in requested
    )
    assert runtime.cleanup["status"] == "incomplete"
    assert runtime.cleanup["rooms"]["8"]["status"] == "retained_busy_room"
