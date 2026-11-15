from pathlib import Path

from playwright.async_api import async_playwright, expect
from test_browser_integration import local_site


async def test_workbench_form_submits_mobile_matrix_and_stateful_strategy():
    static = Path(__file__).resolve().parents[1] / "src/ai_ux/webstatic"
    html = (
        (static / "index.html")
        .read_text()
        .replace(
            '<script src="/static/app.js"></script>',
            "<script>" + (static / "app.js").read_text() + "</script>",
        )
        .encode()
    )
    seen = []
    async with local_site(html) as url, async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page()

        async def route_api(route):
            if route.request.url.endswith("/api/capabilities"):
                return await route.fulfill(
                    json={
                        "devices": [
                            {"id": d, "available": d != "iphone"} for d in ["desktop", "android", "iphone"]
                        ]
                    }
                )
            if route.request.method == "POST":
                seen.append(route.request.post_data_json)
                return await route.fulfill(json={"id": "fixture-job"})
            return await route.fulfill(json={"jobs": []})

        await page.route("**/api/**", route_api)
        try:
            await page.goto(url)
            await expect(page.locator("input[name=devices][value=desktop]")).to_be_checked()
            await expect(page.locator("input[name=devices][value=android]")).to_be_checked()
            await expect(page.locator("input[name=devices][value=iphone]")).to_be_disabled()
            await page.locator("#repo").fill("https://github.com/example/project.git")
            await page.locator("#url").fill("https://example.com/")
            await page.locator("#goal").fill("任务进行中切页，再停止并重试。")
            await page.locator("#form > button").click()
            await page.wait_for_url("**/jobs#fixture-job")
            assert seen[0]["devices"] == ["desktop", "android"]
            assert seen[0]["coverage"] == "stateful"
            assert seen[0]["max_steps"] == 24 and seen[0]["timeout"] == 600
        finally:
            await browser.close()
