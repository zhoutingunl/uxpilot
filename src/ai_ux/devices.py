"""Named browser emulations, always reported separately from physical devices."""

import hashlib
from pathlib import Path

from .schema import Assertion, Browser

PROFILES = {
    "desktop": {"label": "桌面 Chrome", "engine": "chromium", "width": 1440, "height": 1050},
    "android": {
        "label": "Android 触屏模拟",
        "engine": "chromium",
        "width": 412,
        "height": 839,
        "preset": "Pixel 7",
    },
    "iphone": {
        "label": "iPhone / WebKit 模拟",
        "engine": "webkit",
        "width": 390,
        "height": 664,
        "preset": "iPhone 13",
    },
}


def device_browser(base: Browser, name: str) -> Browser:
    profile = PROFILES[name]
    return Browser.model_validate(
        {
            **base.model_dump(),
            "device": name,
            "engine": profile["engine"],
            "width": profile["width"],
            "height": profile["height"],
            "channel": (base.channel if base.engine == "chromium" else "chrome")
            if profile["engine"] == "chromium"
            else None,
            "executable_path": base.executable_path if profile["engine"] == "chromium" else None,
        }
    )


async def available_devices():
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        return [
            {
                "id": key,
                **profile,
                "available": key != "iphone" or Path(playwright.webkit.executable_path).is_file(),
                "physical_device": False,
            }
            for key, profile in PROFILES.items()
        ]


def expand_tasks(tasks, browser, devices):
    expanded = []
    for task in tasks:
        for device in devices:
            base_id = task.scenario_id or task.id
            safe_id = (
                base_id
                if len(base_id) <= 65
                else base_id[:58] + "-" + hashlib.sha256(base_id.encode()).hexdigest()[:6]
            )
            invariants = list(task.invariants)
            if device != "desktop":
                existing = {a.id for a in invariants}
                invariants += [
                    a
                    for a in [
                        Assertion(id="mobile-no-overflow", kind="no_horizontal_overflow"),
                        Assertion(id="mobile-viewport", kind="mobile_viewport"),
                        Assertion(id="mobile-focused-input", kind="focused_input_visible"),
                    ]
                    if a.id not in existing
                ]
            expanded.append(
                task.model_copy(
                    update={
                        "id": f"{safe_id}-{device}",
                        "scenario_id": base_id,
                        "browser": device_browser(task.browser or browser, device),
                        "invariants": invariants,
                    },
                    deep=True,
                )
            )
    return expanded
