"""Review workbench, deployment journal and reports. Never serves the checkout."""

from __future__ import annotations

import argparse
import json
import mimetypes
from collections import deque
from datetime import datetime, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from pydantic import ValidationError

from .review_jobs import JobStore, QueueFull, ReviewRequest

PUBLIC_FIELDS = {
    "repository_commit",
    "workspace_dirty",
    "version",
    "snapshot_sha256",
    "commit",
    "built_at",
    "target",
    "operator",
    "ts",
    "status",
    "selftest",
    "tests",
    "changes",
    "coverage",
    "rollback",
    "note",
    "previous_version",
    "changed_files",
}
ASSETS = {
    "/": "index.html",
    "/index.html": "index.html",
    "/workbench.css": "workbench.css",
    "/workbench.js": "workbench.js",
    "/deploys.html": "deploys.html",
    "/deploys.css": "deploys.css",
    "/deploys.js": "deploys.js",
}
REPORT_SUFFIXES = {".html", ".md", ".json", ".jsonl", ".png", ".mp4", ".webm", ".wav", ".pcm"}


def public(value):
    return {k: v for k, v in value.items() if k in PUBLIC_FIELDS}


class DeploymentState:
    def __init__(self, release: Path, state: Path, reports: Path):
        self.state, self.reports = state, reports.resolve()
        self.current = public(json.loads(release.read_text()))
        self.jobs = JobStore(state / "jobs")

    def snapshot(self):
        records, warnings = deque(maxlen=100), []
        try:
            with (self.state / "deployments.jsonl").open() as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                        if not isinstance(row, dict) or row.get("status") not in {
                            "success",
                            "staged",
                            "failed",
                        }:
                            raise ValueError()
                        row = public(row)
                        coverage = row.get("coverage")
                        if not isinstance(coverage, dict) or coverage.get("snapshot_sha256") != row.get(
                            "snapshot_sha256"
                        ):
                            row["coverage"] = None
                        records.append(row)
                    except (ValueError, TypeError):
                        warnings.append("部分部署记录无法读取")
        except FileNotFoundError:
            pass
        rows = list(reversed(records))
        latest = next((r for r in rows if r["status"] == "success"), None)
        state = "unrecorded"
        if latest:
            state = (
                "ok"
                if all(latest.get(k) == self.current.get(k) for k in ("version", "snapshot_sha256"))
                else "mismatch"
            )
        if rows and rows[0]["status"] == "staged" and rows[0].get("version") == self.current.get("version"):
            state = "staged"
        batches = []
        for directory in sorted(self.reports.glob("*"), reverse=True)[:20]:
            if not directory.is_dir() or directory.is_symlink():
                continue
            report = directory / "report.json"
            runs = []
            try:
                data = json.loads(report.read_text())
                runs = data.get("runs", [])
            except (OSError, ValueError):
                for run in directory.glob("*/run.json"):
                    try:
                        runs.append(json.loads(run.read_text()))
                    except (OSError, ValueError):
                        continue
            if not runs and not (directory / "config.json").exists():
                continue
            batches.append(
                {
                    "id": directory.name,
                    "report": f"reports/{directory.name}/report.html" if report.exists() else None,
                    "runs": [
                        {
                            "task": r.get("task_id"),
                            "outcome": r.get("verification", {}).get("task_outcome"),
                            "judge": r.get("judge_status"),
                            "stop_reason": r.get("stop_reason"),
                        }
                        for r in runs
                    ],
                }
            )
        runtime = {}
        try:
            raw = json.loads((self.state / "evaluation.json").read_text())
            runtime = {k: raw.get(k) for k in ("status", "started_at", "finished_at", "exit_code", "version")}
        except (OSError, ValueError):
            pass
        recent_jobs = self.jobs.list()
        if recent_jobs and recent_jobs[0]["created_at"] > (runtime.get("started_at") or ""):
            job = recent_jobs[0]
            runtime = {
                "status": "running" if job["status"] in {"planning", "evaluating"} else job["status"],
                "started_at": job.get("started_at") or job["created_at"],
                "finished_at": job.get("finished_at"),
                "exit_code": job.get("exit_code"),
                "version": job.get("version"),
            }
        return {
            "current": self.current,
            "state": state,
            "records": rows,
            "warnings": sorted(set(warnings)),
            "evaluation": runtime,
            "batches": batches,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def report_file(self, path):
        relative = Path(unquote(path.removeprefix("/reports/")))
        if relative.is_absolute() or any(p.startswith(".") for p in relative.parts):
            return None
        candidate = self.reports / relative
        if any(p.is_symlink() for p in [candidate, *candidate.parents] if p != self.reports):
            return None
        resolved = candidate.resolve()
        if (
            not resolved.is_relative_to(self.reports)
            or resolved.suffix not in REPORT_SUFFIXES
            or not resolved.is_file()
        ):
            return None
        return resolved


class Handler(BaseHTTPRequestHandler):
    timeout = 15

    def __init__(self, *args, deployment, **kwargs):
        self.deployment = deployment
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        # No arbitrary request URLs or query strings in logs.
        pass

    def json_response(self, status, data):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        if urlsplit(self.path).path != "/api/reviews":
            self.json_response(404, {"error": "接口不存在"})
            return
        host = self.headers.get("Host", "")
        allowed = {self.server.server_address[0], "127.0.0.1", "localhost"}
        try:
            request_host = urlsplit("http://" + host).hostname
            origin = self.headers.get("Origin")
            same_origin = not origin or (
                urlsplit(origin).scheme in {"http", "https"} and urlsplit(origin).netloc == host
            )
            if request_host not in allowed or not same_origin or self.headers.get("X-UX-Request") != "1":
                self.json_response(403, {"error": "请从本网站的评测表单提交"})
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self.json_response(415, {"error": "请求格式不正确"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 12000 or self.headers.get("Transfer-Encoding"):
                self.json_response(413, {"error": "提交内容过长或为空"})
                return
            request = ReviewRequest.model_validate(json.loads(self.rfile.read(length)))
            job, created = self.deployment.jobs.submit(request)
            self.json_response(202, {"job": JobStore.public(job), "created": created})
        except QueueFull:
            self.json_response(429, {"error": "当前已有 5 项评测等待处理，请稍后再提交。"})
        except (ValueError, ValidationError):
            self.json_response(
                400,
                {
                    "error": "请检查输入：仓库使用 HTTPS Git 地址，网站使用 HTTP(S) 地址，不含密码或密钥；任务数为 1–5。"
                },
            )
        except OSError:
            self.json_response(503, {"error": "暂时无法保存评测任务，请稍后重试。"})

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head=False):
        path = urlsplit(self.path).path
        payload, mime, file = None, "application/json; charset=utf-8", None
        if path == "/api/health":
            payload = json.dumps({"status": "ok", "release": self.deployment.current}).encode()
        elif path == "/api/reviews":
            payload = json.dumps(
                {"jobs": [JobStore.public(j) for j in self.deployment.jobs.list()[:50]]}, ensure_ascii=False
            ).encode()
        elif path == "/api/deployments":
            try:
                payload = json.dumps(self.deployment.snapshot(), ensure_ascii=False).encode()
            except (OSError, ValueError, TypeError):
                self.send_error(503, "Deployment state unavailable")
                return
        elif path in ASSETS:
            file = Path(__file__).parent / "web" / ASSETS[path]
        elif path.startswith("/reports/"):
            file = self.deployment.report_file(path)
        if file and file.is_file():
            mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        elif payload is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload) if payload is not None else file.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'none'",
        )
        self.end_headers()
        if not head:
            try:
                if payload is not None:
                    self.wfile.write(payload)
                else:
                    with file.open("rb") as stream:
                        while block := stream.read(65536):
                            self.wfile.write(block)
            except (BrokenPipeError, ConnectionResetError):
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--release", type=Path, default=Path("release.json"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--reports", type=Path, required=True)
    args = parser.parse_args()
    deployment = DeploymentState(args.release.resolve(), args.state, args.reports)
    server = ThreadingHTTPServer((args.host, args.port), partial(Handler, deployment=deployment))
    server.serve_forever()


if __name__ == "__main__":
    main()
