import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import async_playwright, expect

from ai_ux import webapp


@asynccontextmanager
async def stats_site(payload):
    static = Path(webapp.__file__).parent / "webstatic"

    async def serve(reader, writer):
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            path = request.split(b" ")[1].decode().split("?")[0]
            if path == "/api/stats":
                body = json.dumps(payload).encode()
                mime = "application/json"
            elif path in ["/stats", "/static/stats.js", "/static/style.css"]:
                name = "stats.html" if path == "/stats" else path.rsplit("/", 1)[1]
                body = (static / name).read_bytes()
                mime = {"stats.html": "text/html", "stats.js": "text/javascript", "style.css": "text/css"}[
                    name
                ]
            else:
                body = b""
                mime = "text/plain"
            writer.write(
                f"HTTP/1.1 200 OK\r\nContent-Type: {mime}; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/stats"
    finally:
        server.close()
        await server.wait_closed()


def fixture():
    empty = {"count": 0, "mean": None, "p50": None, "p90": None, "p95": None, "buckets": []}
    return {
        "start": "2026-11-02",
        "end": "2026-11-04",
        "timezone": "Asia/Shanghai",
        "submitted": 12,
        "known_total_tokens": 109,
        "unknown_usage_calls": 1,
        "states": {},
        "notes": [],
        "total_seconds": empty,
        "queue_seconds": empty,
        "execution_seconds": empty,
        "daily": [
            {"date": "2026-11-03", "submitted": 2, "finished": 1, "total_tokens": 9},
            {"date": "2026-11-02", "submitted": 0, "finished": 0, "total_tokens": 0},
            {"date": "2026-11-04", "submitted": 10, "finished": 2, "total_tokens": 100},
        ],
        "models": [
            {
                "model": "Beta",
                "calls": 2,
                "errors": 0,
                "known_usage_calls": 1,
                "total_tokens": 9,
                "duration_ms": 100,
            },
            {
                "model": "Alpha",
                "calls": 10,
                "errors": 2,
                "known_usage_calls": 3,
                "total_tokens": 100,
                "duration_ms": 20,
            },
            {
                "model": "Zero",
                "calls": 1,
                "errors": 1,
                "known_usage_calls": 1,
                "total_tokens": 0,
                "duration_ms": 0,
            },
            {
                "model": "Unknown",
                "calls": 3,
                "errors": 0,
                "known_usage_calls": 0,
                "total_tokens": 0,
                "duration_ms": 50,
            },
        ],
    }


async def test_stats_headers_sort_numbers_dates_text_and_unknown_values():
    async with stats_site(fixture()) as url, async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url)
            daily = page.locator("#daily-table tbody tr td:first-child")
            models = page.locator("#models-table tbody tr td:first-child")
            await expect(daily).to_have_text(["2026-11-04", "2026-11-03", "2026-11-02"])
            await expect(models).to_have_text(["Alpha", "Unknown", "Beta", "Zero"])
            for key, expected in [
                ("model", ["Alpha", "Beta", "Unknown", "Zero"]),
                ("calls", ["Alpha", "Unknown", "Beta", "Zero"]),
                ("errors", ["Alpha", "Zero", "Beta", "Unknown"]),
                ("total_tokens", ["Alpha", "Beta", "Zero", "Unknown"]),
                ("duration_ms", ["Beta", "Unknown", "Alpha", "Zero"]),
            ]:
                await page.locator(f'#models-table th[data-sort="{key}"] button').click()
                await expect(models).to_have_text(expected)
            await page.locator('#models-table th[data-sort="duration_ms"] button').press("Enter")
            await expect(models).to_have_text(["Zero", "Alpha", "Unknown", "Beta"])
            await expect(page.locator('#models-table th[data-sort="duration_ms"]')).to_have_attribute(
                "aria-sort", "ascending"
            )
            await page.locator('#models-table th[data-sort="total_tokens"] button').click()
            await page.locator('#models-table th[data-sort="total_tokens"] button').click()
            await expect(models).to_have_text(["Zero", "Beta", "Alpha", "Unknown"])
            await expect(page.locator("#models-table tbody tr td:nth-child(4)")).to_have_text(
                ["0", "9", "100", "—"]
            )
            await expect(daily).to_have_text(["2026-11-04", "2026-11-03", "2026-11-02"])
            for key in ["date", "submitted", "finished", "total_tokens"]:
                header = page.locator(f'#daily-table th[data-sort="{key}"] button')
                await header.click()
                if key == "date":
                    await expect(daily).to_have_text(["2026-11-02", "2026-11-03", "2026-11-04"])
                else:
                    await expect(daily).to_have_text(["2026-11-04", "2026-11-03", "2026-11-02"])
                await header.click()
                expected = (
                    ["2026-11-04", "2026-11-03", "2026-11-02"]
                    if key == "date"
                    else ["2026-11-02", "2026-11-03", "2026-11-04"]
                )
                await expect(daily).to_have_text(expected)
        finally:
            await browser.close()


async def test_stats_empty_state_does_not_show_a_zero_filled_calendar():
    data = fixture()
    data.update(start=None, models=[], daily=[])
    async with stats_site(data) as url, async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url)
            await expect(page.locator("#stats-range")).to_contain_text("暂无评测记录")
            await expect(page.locator("#daily-table tbody")).to_have_text("暂无提交记录")
            await expect(page.locator("#models-table tbody")).to_have_text("暂无模型调用记录")
            await expect(page.locator(".duration-empty")).to_have_text(["暂无耗时样本"] * 3)
            await expect(page.locator(".duration-value")).to_have_text(["—"] * 12)
            await expect(page.locator(".duration-chart-row")).to_have_count(0)
        finally:
            await browser.close()


async def test_duration_charts_align_columns_and_render_real_bar_widths_on_desktop_and_mobile():
    data = fixture()
    buckets = [
        ["≤30s", 0],
        ["30–60s", 0],
        ["1–2min", 1],
        ["2–5min", 1],
        ["5–10min", 0],
        ["10–20min", 1],
        [">20min", 0],
    ]
    data["total_seconds"] = {
        "count": 3,
        "mean": 316.55,
        "p50": 152.284,
        "p90": 736.26,
        "p95": 736.26,
        "buckets": buckets,
    }
    data["execution_seconds"] = {**data["total_seconds"], "mean": 316.336, "p50": 151.641}
    data["queue_seconds"] = {
        "count": 3,
        "mean": 0.214,
        "p50": 0,
        "p90": 0.643,
        "p95": 0.643,
        "buckets": [[label, 3 if i == 0 else 0] for i, (label, _) in enumerate(buckets)],
    }
    async with stats_site(data) as url, async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url)
            total = page.locator('.duration-distribution[aria-label="总耗时分布"]')
            queue = page.locator('.duration-distribution[aria-label="排队耗时分布"]')
            await expect(total.locator(".duration-value")).to_have_text(
                ["316.55", "152.28", "736.26", "736.26"]
            )
            await expect(total.locator(".duration-unit")).to_have_text(["秒"] * 4)
            await expect(total.locator("dd").nth(1)).to_have_attribute("title", "152.284 秒")
            await expect(queue.locator(".duration-value")).to_have_text(["0.21", "0.00", "0.64", "0.64"])
            await expect(total.locator(".duration-count")).to_have_text(["0", "0", "1", "1", "0", "1", "0"])
            await expect(total.locator(".duration-percent")).to_have_text(
                ["0%", "0%", "33.3%", "33.3%", "0%", "33.3%", "0%"]
            )
            for width in (1440, 390):
                await page.set_viewport_size({"width": width, "height": 1050})
                dimensions = await total.locator(".duration-chart-row").evaluate_all("""rows => rows.map(row => {
                  const rect = node => {const r=node.getBoundingClientRect();return {x:r.x,right:r.right,cy:r.y+r.height/2,width:r.width,height:r.height}};
                  return {label:rect(row.querySelector('.duration-bucket')),track:rect(row.querySelector('.duration-track')),
                    fill:rect(row.querySelector('.duration-fill')),count:rect(row.querySelector('.duration-count')),
                    percent:rect(row.querySelector('.duration-percent'))};
                })""")
                for row in dimensions:
                    assert row["track"]["width"] > 20 and row["track"]["height"] >= 8
                    assert row["label"]["right"] <= row["track"]["x"]
                    assert row["track"]["right"] <= row["count"]["x"]
                    assert row["count"]["right"] <= row["percent"]["x"]
                    assert (
                        max(v["cy"] for k, v in row.items() if k != "fill")
                        - min(v["cy"] for k, v in row.items() if k != "fill")
                        < 2
                    )
                assert dimensions[0]["fill"]["width"] == 0
                assert abs(dimensions[2]["fill"]["width"] / dimensions[2]["track"]["width"] - 1 / 3) < 0.01
                full = await queue.locator(".duration-chart-row").first.evaluate(
                    "row => row.querySelector('.duration-fill').getBoundingClientRect().width / row.querySelector('.duration-track').getBoundingClientRect().width"
                )
                assert abs(full - 1) < 0.01
                assert await total.evaluate("element => element.scrollWidth <= element.clientWidth")
                assert await page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
        finally:
            await browser.close()
