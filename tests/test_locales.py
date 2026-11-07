import json

import pytest
from test_browser_integration import local_site
from test_llm import providers

from ai_ux.locales import check_locales
from ai_ux.schema import App, Browser, Config, Task


def fixture(overflow):
    return (
        """<!doctype html><html lang="zh-CN"><body style="margin:0;WIDTH">
    <header class="site-topbar"><nav><a href="index.html">首页</a></nav></header>
    <button id="lang-switch-button">语言</button><div class="lang-menu" hidden>
    <button data-lang="zh">中文</button><button data-lang="en">English</button><button data-lang="fr">Français</button></div>
    <div class="room-links"><a data-room="7" aria-current="page">Room 7</a></div><canvas width="100" height="100"></canvas>
    <select id="voice-mode" disabled><option value="local_first">Local</option><option value="cloud">Cloud</option></select>
    <script>
    const menu=document.querySelector('.lang-menu'),mode=document.querySelector('#voice-mode');
    let modesReady=false;
    function updateMode(lang){mode.value=lang==='zh'?'local_first':'cloud';mode.options[0].disabled=lang!=='zh';}
    function language(lang){document.documentElement.lang=lang==='zh'?'zh-CN':lang;localStorage.setItem('lang',lang);
      const url=new URL(location.href);url.searchParams.delete('lang');url.searchParams.set('room','7');history.replaceState(null,'',url);
      document.querySelector('nav a').textContent={zh:'首页',en:'Home',fr:'Accueil'}[lang];
      for(const option of menu.children)option.setAttribute('aria-current',String(option.dataset.lang===lang));
      if(modesReady)updateMode(lang);menu.hidden=true;}
    language(new URL(location.href).searchParams.get('lang')||localStorage.getItem('lang')||'zh');
    document.querySelector('#lang-switch-button').onclick=()=>menu.hidden=!menu.hidden;
    for(const option of menu.children)option.onclick=()=>language(option.dataset.lang);
    setTimeout(()=>{modesReady=true;mode.disabled=false;updateMode(document.documentElement.lang.split('-')[0])},700);
    </script></body></html>""".replace("WIDTH", "width:1600px" if overflow else "")
    ).encode()


@pytest.mark.parametrize("overflow", [False, True])
async def test_real_language_menu_reload_navigation_canvas_and_mobile_layout(tmp_path, monkeypatch, overflow):
    async with local_site(fixture(overflow)) as url:
        config = Config(
            app=App(name="Duck", base_url=url, allowed_hosts=["127.0.0.1"], adapter="duckking"),
            models=providers(monkeypatch),
            browser=Browser(video=False),
            output_dir=str(tmp_path),
            tasks=[Task(id="locale", goal="Test languages")],
        )
        # The fixture has no scene API; its cleanup returns 200 and does not
        # affect the language checks. No paid model or public site is involved.
        root, result = await check_locales(
            config, pages=["index.html", "statistics.html"], viewports=["mobile"]
        )
        assert len(result["cases"]) == 14
        assert result["status"] == ("failed" if overflow else "passed")
        switches = [c for c in result["cases"] if c["phase"] == "switch"]
        assert all(
            c["checks"]["same_document"] and c["checks"]["same_room"] and c["checks"]["canvas_preserved"]
            for c in switches
        )
        assert all(c["checks"]["no_horizontal_overflow"] is not overflow for c in switches)
        assert (root / "locale-report.html").is_file()
        assert json.loads((root / "locale-report.json").read_text())["status"] == result["status"]
        assert all((root / c["screenshot"]).is_file() for c in switches)
