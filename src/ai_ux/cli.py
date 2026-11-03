from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from .agent import evaluate, rejudge
from .discovery import discover
from .llm import read_key
from .probe import probe
from .schema import load_config


def evaluation_exit(result):
    if any(r["stop_reason"] == "interrupted" for r in result["runs"]):
        return 130
    return (
        2
        if any(
            r["stop_reason"] == "runtime_error" or r["judge_status"] != "completed" for r in result["runs"]
        )
        else 0
    )


def main():
    parser = argparse.ArgumentParser(
        prog="uxpilot", description="真实浏览器体验、独立验证与原生视频 UX Judge"
    )
    parser.add_argument("--config", type=Path, default=Path("configs/duckking.yaml"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-config", help="校验配置，不联网")
    commands.add_parser("doctor", help="检查环境和凭据是否存在，不打印密钥")
    pr = commands.add_parser("probe-video", help="给两个供应商发送带随机代码的短视频，验证原生视频识别")
    pr.add_argument("--output", type=Path)
    review = commands.add_parser("judge", help="复用已有浏览器证据重跑 Judge，保留旧评审")
    review.add_argument("run_dir", type=Path)
    run = commands.add_parser("eval", help="执行配置任务并保存 HTML/Markdown/JSON 报告")
    run.add_argument("--task", action="append", dest="tasks")
    run.add_argument("--repeats", type=int, choices=range(1, 6))
    project = commands.add_parser("review", help="从仓库路径/Git URL 与演示地址自动规划并执行通用体验")
    project.add_argument("--repo", required=True, help="本地目录或 HTTPS/SSH Git 仓库 URL")
    project.add_argument("--url", required=True, help="演示页面完整 URL（保留路径/查询参数）")
    project.add_argument("--ref", help="远程仓库分支或 tag；不修改本地 checkout")
    project.add_argument("--goal", action="append", default=[], help="可选具体体验目标，可重复")
    project.add_argument("--tasks", type=int, default=3, choices=range(1, 6), help="自动规划任务上限")
    project.add_argument("--output", type=Path, default=Path("artifacts/projects"))
    project.add_argument("--plan-only", action="store_true", help="生成可复用 project.yaml，不执行体验任务")
    project.add_argument("--allow-host", action="append", default=[], help="额外允许导航的精确域名")
    project.add_argument("--resource-host", action="append", default=[], help="仅允许加载资源的 CDN/API 域名")
    project.add_argument("--adapter", choices=["generic", "duckking"], default="generic")
    project.add_argument("--max-steps", type=int, default=12, choices=range(1, 41))
    project.add_argument("--timeout", type=int, default=360, help="每任务秒数，包含 LLM 决策")
    project.add_argument("--repeats", type=int, default=1, choices=range(1, 6))
    args = parser.parse_args()
    try:
        if args.command == "review":
            # This entry point does not load the DuckKing task profile.
            async def review_project():
                if not 15 <= args.timeout <= 1800:
                    raise ValueError("--timeout must be 15..1800 seconds")
                project_root, config, repository = await discover(
                    args.repo,
                    args.url,
                    output=args.output,
                    ref=args.ref,
                    goals=args.goal,
                    task_count=args.tasks,
                    allowed_hosts=args.allow_host,
                    resource_hosts=args.resource_host,
                    adapter=args.adapter,
                    task_steps=args.max_steps,
                    task_timeout=args.timeout,
                )
                if args.plan_only:
                    return 0
                _, result = await evaluate(config, repeats=args.repeats, repository=repository)
                return evaluation_exit(result)

            return asyncio.run(review_project())
        config = load_config(args.config.resolve())
        if args.command == "validate-config":
            print(json.dumps({"valid": True, "tasks": [t.id for t in config.tasks]}, ensure_ascii=False))
            return 0
        if args.command == "doctor":
            print(
                json.dumps(
                    {
                        "python": sys.version.split()[0],
                        "ffmpeg": bool(shutil.which("ffmpeg")),
                        "ffprobe": bool(shutil.which("ffprobe")),
                        "browser": config.browser.channel
                        or config.browser.executable_path
                        or "bundled chromium",
                        "providers": [
                            {"provider": p.name, "model": p.model, "credential_available": bool(read_key(p))}
                            for p in [config.models.primary, config.models.fallback]
                        ],
                        "tasks": len(config.tasks),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "probe-video":
            output = args.output or Path(config.output_dir) / (
                "video-probe-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            )
            result = asyncio.run(probe(config, output))
            return 0 if all(r["passed"] for r in result["providers"]) else 2
        if args.command == "judge":
            result = asyncio.run(rejudge(config, args.run_dir.resolve()))
            return 0 if result["judge_status"] == "completed" else 2
        _, result = asyncio.run(evaluate(config, args.tasks, args.repeats))
        return evaluation_exit(result)
    except KeyboardInterrupt:
        print("Interrupted; completed artifacts retained.", file=sys.stderr)
        return 130
    except Exception as e:
        # Do not emit request objects, payloads or credentials in CLI tracebacks.
        print(f"ai-ux failed: {type(e).__name__}: {str(e)[:700]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
