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
        finally:
            await browser.close()
