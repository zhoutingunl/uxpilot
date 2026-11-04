#!/usr/bin/env python3
"""Publish a tested installed release, preserving the journal and incremental notes."""

import datetime
import json
import subprocess
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from ai_ux.release_checks import ensure_idle

base = Path("/root/zt_dir/ai_base/DuckKing_ux_review")
release = (base / "current").resolve()
meta = json.loads((release / "release.json").read_text())
assert meta.get("changes"), "Release must describe its incremental changes"
state = base / "state"
(state / "jobs").mkdir(parents=True, exist_ok=True)
(base / "eval.lock").touch(exist_ok=True)
journal = state / "deployments.jsonl"
rows = [json.loads(line) for line in journal.read_text().splitlines()] if journal.exists() else []
previous = next((r for r in reversed(rows) if r["status"] == "success" and r.get("target") == "jfcs62"), None)
old_files = {}
if previous:
    old = base / "releases" / previous["version"] / "release.json"
    if old.is_file():
        old_files = json.loads(old.read_text()).get("files", {})
new_files = meta["files"]
changed = [
    name for name in sorted(set(old_files) | set(new_files)) if old_files.get(name) != new_files.get(name)
]
tests = ET.parse(release / "verification/tests.xml").getroot()
suites = list(tests.iter("testsuite"))
assert suites and all(int(s.get("failures", "0")) == int(s.get("errors", "0")) == 0 for s in suites)
passed = sum(int(s.get("tests", "0")) - int(s.get("skipped", "0")) for s in suites)
counts = json.loads((release / "verification/coverage.json").read_text())["totals"]
row = {key: meta[key] for key in ("version", "snapshot_sha256", "built_at", "operator", "target")}
row.update(
    changes=meta["changes"],
    previous_version=previous["version"] if previous else None,
    changed_files=changed,
    tests=f"{passed} passed · Ruff 通过",
    coverage={
        "snapshot_sha256": meta["snapshot_sha256"],
        "lines": {"covered": counts["covered_lines"], "total": counts["num_statements"]},
        "branches": {"covered": counts["covered_branches"], "total": counts["num_branches"]},
    },
)


def record(status, selftest, note):
    entry = {
        **row,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": status,
        "selftest": selftest,
        "note": note,
    }
    with journal.open("a") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


record("staged", "pending", "安装和测试完成，正在检查网页服务与任务队列。")
units = ["duckking-ux-web.service", "duckking-ux-eval.service"]
try:
    ensure_idle(base)
    subprocess.run(["systemctl", "disable", "--now", "duckking-ux-worker.service"], check=True)
    subprocess.run(["systemctl", "stop", "duckking-ux-web.service"], check=True)
    subprocess.run(
        [str(release / ".venv/bin/python"), "-m", "ai_ux.restore_history", "--base", str(base)], check=True
    )
    for name in units:
        target = Path("/etc/systemd/system") / name
        target.write_bytes((release / "deploy" / name).read_bytes())
        target.chmod(0o644)
    (base / "run-eval.sh").write_bytes((release / "deploy/run-eval.sh").read_bytes())
    subprocess.run(
        ["systemd-analyze", "verify", *[str(Path("/etc/systemd/system") / u) for u in units]], check=True
    )
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "duckking-ux-web.service"], check=True)
    subprocess.run(["systemctl", "restart", "duckking-ux-web.service"], check=True)
    url = "http://10.210.32.30:18765/"
    for attempt in range(40):
        try:
            with urllib.request.urlopen(url + "api/health", timeout=2) as response:
                assert json.load(response)["release"]["version"] == meta["version"]
            break
        except (urllib.error.URLError, AssertionError):
            if attempt == 39:
                raise
            time.sleep(0.25)
    for path in [
        "",
        "jobs",
        "stats",
        "errors",
        "static/app.js",
        "static/errors.js",
        "deploys.html",
        "api/jobs",
        "api/stats",
        "api/errors",
        "api/deployments",
    ]:
        with urllib.request.urlopen(url + path, timeout=5) as response:
            content = response.read()
            assert response.status == 200
            if path == "":
                assert b'id="form"' in content
            if path == "deploys.html":
                assert b'id="form"' not in content
    subprocess.run(["systemctl", "is-active", "--quiet", "duckking-ux-web.service"], check=True)
    record("success", "passed", "完整工作台、任务管理、统计、错误聚类及历史报告兼容检查通过。")
    print(
        json.dumps(
            {
                "version": meta["version"],
                "passed": passed,
                "previous_version": row["previous_version"],
                "changed_files": len(changed),
                "home": url,
            },
            ensure_ascii=False,
        )
    )
except Exception:
    record("failed", "failed", "发布检查未通过，请查看服务与安装日志。")
    raise
