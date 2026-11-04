"""Deterministic usage, latency and tool-error aggregation; no model guesses."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .policy import scrub

ERROR_RULES = [
    (
        "policy",
        r"outside.*(?:scope|allowlist)|policy|forbidden|not present in the latest|cannot switch.*room|not.*observed|blockedbyclient",
    ),
    (
        "authentication",
        r"credential_missing|http[_ ](?:401|403)|unauthorized|authentication|permission denied",
    ),
    ("rate_limit", r"429|rate.?limit|too many requests"),
    ("upstream", r"(?:http|provider_error)[_ ](?:5\d\d)|overload|server error"),
    ("timeout", r"timeout|timed out|time budget"),
    ("schema", r"invalid_json_schema|validationerror|invalid.*(?:schema|envelope)|truncated_or_empty"),
    ("not_found", r"not found|missing|http[_ ]404|no such file"),
    ("network", r"connect|network|fetch|dns|err_(?:connection|name|internet)|socket"),
    ("invalid_input", r"invalid|requires|unsupported|unknown task|argument"),
]


def classify_error(tool: str, message: str) -> dict:
    text = str(scrub(message))[:2500]
    lower = text.lower()
    category = next((kind for kind, pattern in ERROR_RULES if re.search(pattern, lower)), "other")
    signature = re.sub(r'https?://[^\s\)\]"\']+', "<url>", lower)
    signature = re.sub(r"(?<!\w)[a-f0-9]{8,}(?:-[a-f0-9-]+)?\b", "<id>", signature)
    signature = re.sub(r"([a-z][\w-]*-\d{2}-)[a-f0-9]{6}\b", "<run>", signature)
    signature = re.sub(r"\b(?:n\d+|\d+(?:\.\d+)?(?:ms|s|秒))\b", "<value>", signature)
    signature = re.sub(r"\b\d{4,}\b", "<number>", signature)
    signature = " ".join(signature.split())[:220]
    key = hashlib.sha256(f"{tool}\0{category}\0{signature}".encode()).hexdigest()[:20]
    return {"cluster_id": key, "tool": tool, "category": category, "signature": signature, "message": text}


def event_error(event: dict):
    kind = event.get("kind")
    if kind == "agent_action_result" and event.get("ok") is False:
        return classify_error(
            "browser." + event.get("action_kind", "action"), event.get("error", "action_failed")
        )
    if kind == "model_call" and event.get("status") == "error":
        return classify_error(
            "llm." + str(event.get("provider", "unknown")), event.get("error", "model_failed")
        )
    if kind in {"stage_error", "cleanup_error", "pageerror", "request_failed", "websocket_error"}:
        return classify_error(
            str(event.get("stage") or kind), str(event.get("error") or event.get("failure") or kind)
        )
    if kind == "http_error":
        return classify_error("page.http", f"http_{event.get('status')} {event.get('url', '')}")
    return None


def usage_numbers(usage):
    usage = usage if isinstance(usage, dict) else {}

    def number(key):
        value = usage.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    prompt, completion, total = number("prompt_tokens"), number("completion_tokens"), number("total_tokens")
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def distribution(values):
    values = sorted(float(v) for v in values if v is not None and math.isfinite(v) and v >= 0)
    if not values:
        return {"count": 0, "mean": None, "p50": None, "p90": None, "p95": None, "buckets": []}

    def quantile(q):
        return round(values[max(0, math.ceil(len(values) * q) - 1)], 3)

    thresholds = [30, 60, 120, 300, 600, 1200]
    counts = [0] * (len(thresholds) + 1)
    for v in values:
        index = next((i for i, t in enumerate(thresholds) if v <= t), len(thresholds))
        counts[index] += 1
    labels = ["≤30s", "30–60s", "1–2min", "2–5min", "5–10min", "10–20min", ">20min"]
    return {
        "count": len(values),
        "mean": round(sum(values) / len(values), 3),
        "p50": quantile(0.5),
        "p90": quantile(0.9),
        "p95": quantile(0.95),
        "buckets": list(zip(labels, counts)),
    }


def statistics_period(days: int, now: datetime | None = None, tz="Asia/Shanghai"):
    """Include today in the requested number of local calendar days."""
    if days < 1:
        raise ValueError("days must be positive")
    end = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz))
    start = datetime.combine(end.date() - timedelta(days=days - 1), time.min, end.tzinfo)
    return start, end


def statistics(jobs, calls, start, end, tz="Asia/Shanghai", first_activity=None):
    zone = ZoneInfo(tz)
    if first_activity is None:
        first_activity = min([j["created_at"] for j in jobs] + [c["at"] for c in calls], default=None)
    first_date = datetime.fromtimestamp(first_activity, zone).date() if first_activity is not None else None
    effective_start = max(start, first_date) if first_date is not None else None
    days = {
        (effective_start + timedelta(days=i)).isoformat(): {"submitted": 0, "finished": 0, "total_tokens": 0}
        for i in range((end - effective_start).days + 1 if effective_start is not None else 0)
    }
    states = Counter()
    execution = []
    queue = []
    total = []
    for job in jobs:
        day = datetime.fromtimestamp(job["created_at"], zone).date().isoformat()
        if day not in days:
            continue
        days[day]["submitted"] += 1
        states[job["status"]] += 1
        if job.get("started_at") is not None:
            queue.append(max(0, job["started_at"] - job["created_at"]))
        if job.get("finished_at") is not None:
            days[day]["finished"] += 1
            total.append(job["finished_at"] - job["created_at"])
            if job.get("started_at") is not None:
                execution.append(job["finished_at"] - job["started_at"])
    models = defaultdict(
        lambda: {
            "calls": 0,
            "errors": 0,
            "known_usage_calls": 0,
            "unknown_usage_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "duration_ms": 0,
        }
    )
    for call in calls:
        day = datetime.fromtimestamp(call["at"], zone).date().isoformat()
        if day not in days:
            continue
        key = f"{call['provider']}/{call['model']}"
        row = models[key]
        row["calls"] += 1
        row["errors"] += call["status"] != "ok"
        row["duration_ms"] += call["duration_ms"] or 0
        usage = usage_numbers(call)
        row["known_usage_calls"] += usage["total_tokens"] is not None
        row["unknown_usage_calls"] += usage["total_tokens"] is None
        for name, value in usage.items():
            row[name] += value or 0
        days[day]["total_tokens"] += usage["total_tokens"] or 0
    return {
        "timezone": tz,
        "start": effective_start.isoformat() if days else None,
        "window_start": start.isoformat(),
        "first_recorded_date": first_date.isoformat() if first_date else None,
        "end": end.isoformat(),
        "submitted": sum(d["submitted"] for d in days.values()),
        "states": dict(states),
        "daily": [{"date": day, **days[day]} for day in sorted(days, reverse=True)],
        "execution_seconds": distribution(execution),
        "queue_seconds": distribution(queue),
        "total_seconds": distribution(total),
        "models": [{"model": key, **row} for key, row in models.items()],
        "known_total_tokens": sum(r["total_tokens"] for r in models.values()),
        "unknown_usage_calls": sum(r["unknown_usage_calls"] for r in models.values()),
        "notes": [
            "每日计数为评测任务数（含迁入的历史命令行批次），非 Git commit 数。",
            "提交与耗时按提交日期筛选；Token 按调用发生日期统计。",
            "失败调用缺失用量不补为零；已知用量不是账单。",
            "执行耗时含模型等待，排队时间单列；已结束样本才计入耗时分布。",
        ],
    }
