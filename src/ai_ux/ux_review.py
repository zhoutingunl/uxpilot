"""Portable, evidence-scoped UI/UX Pro Max guidance for the independent Judge."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .schema import UXReview

RULES = Path(__file__).resolve().parent / "data" / "ux-review-rules.json"


def review_context(settings: UXReview) -> dict:
    if not settings.enabled:
        return {"enabled": False, "max_suggestions": 0, "rules": []}
    raw = RULES.read_bytes()
    return {
        **json.loads(raw),
        "enabled": True,
        "max_suggestions": settings.max_suggestions,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def validate_suggestions(judgement, context, evidence, references, video_info):
    """Keep recommendations out of defect counts; reject fabricated rules/evidence/clocks."""
    rules = {r["id"] for r in context["rules"]}
    has_video = video_info.get("status") == "ready"
    duration = (video_info.get("review") or video_info).get("duration_seconds")
    valid, rejected = [], []
    for suggestion in judgement.ux_suggestions:
        reason = None
        if not context["enabled"]:
            reason = "ux_review_disabled"
        elif not set(suggestion.rule_refs).issubset(rules):
            reason = "unknown_ux_rule"
        else:
            try:
                for ref in suggestion.evidence_refs:
                    if ref not in references:
                        raise ValueError("unsupplied evidence")
                    evidence.resolve(ref)
                if not any(
                    ref.endswith((".png", ".jsonl")) or ref.startswith("observations/")
                    for ref in suggestion.evidence_refs
                ):
                    raise ValueError("concrete evidence required")
            except ValueError:
                reason = "missing_or_nonconcrete_evidence"
        if reason is None:
            timestamps = suggestion.video_timestamps
            if has_video and not timestamps:
                reason = "missing_video_timestamp"
            for timestamp in timestamps:
                match = re.fullmatch(r"([0-9]{2}):([0-5][0-9])", timestamp)
                seconds = int(match[1]) * 60 + int(match[2]) if match else None
                if not has_video or seconds is None or (duration is not None and seconds > duration):
                    reason = "invalid_video_timestamp"
                    break
        if reason is None and len(valid) >= context["max_suggestions"]:
            reason = "suggestion_limit"
        if reason:
            rejected.append({"title": suggestion.title, "reason": reason})
        else:
            valid.append(suggestion)
    judgement.ux_suggestions = valid
    return rejected
