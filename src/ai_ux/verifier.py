from __future__ import annotations


def completed_actions(events):
    return [
        e
        for e in events
        if e["kind"] == "product_action"
        and e["data"].get("kind") == "result"
        and e["data"].get("status") == "completed"
    ]


def check_events(assertion, events):
    if assertion.kind == "action_completed":
        return any(
            e["data"].get("text", "").split(" · ")[0] == assertion.action_name
            for e in completed_actions(events)
        )
    if assertion.kind == "voice_transcript":
        return any(
            e["kind"] == "voice_event"
            and e.get("direction") == "received"
            and e["data"].get("type") == "transcript"
            and (not assertion.role or e["data"].get("role") == assertion.role)
            and (not assertion.source or e["data"].get("source", "cloud") == assertion.source)
            and (not assertion.contains or assertion.contains in e["data"].get("text", ""))
            for e in events
        )
    if assertion.kind == "audio_received":
        return any(e["kind"] == "first_audio" for e in events)
    if assertion.kind == "model_before_action":
        visible = [
            e["t_ms"]
            for e in events
            if e["kind"] == "display_state"
            and (
                e["data"].get("model") == "official"
                or (
                    e["data"].get("scene_placeholder_hidden") is True
                    and '"result":"ready"' in (e["data"].get("scene_timing") or "")
                )
            )
        ]
        actions = [
            e["t_ms"]
            for e in events
            if e["kind"] == "product_action"
            and e["data"].get("kind") == "action"
            and not e["data"].get("text", "").startswith(("开始动作 · scene_", "开始动作 · stop"))
        ]
        if not actions:
            return False
        return bool(visible) and min(visible) <= min(actions)
    return None


async def verify(runtime, assertions):
    rows = []
    for assertion in assertions:
        try:
            result = check_events(assertion, runtime.evidence.events)
            if result is None:
                result = await runtime.inspect_assertion(assertion)
            rows.append(
                {
                    "id": assertion.id,
                    "kind": assertion.kind,
                    "required": assertion.required,
                    "scope": assertion.scope,
                    "status": "passed" if result is True else "failed",
                    "evidence_refs": ["events.jsonl"],
                }
            )
        except Exception:
            rows.append(
                {
                    "id": assertion.id,
                    "kind": assertion.kind,
                    "required": assertion.required,
                    "scope": assertion.scope,
                    "status": "error",
                    "error": "verification_observation_failed",
                    "evidence_refs": ["events.jsonl"],
                }
            )
    required = [r for r in rows if r["required"] and r["scope"] == "task"]
    health = [r for r in rows if r["scope"] == "page_health"]
    outcome = (
        "not_verified"
        if not required
        else "passed"
        if all(r["status"] == "passed" for r in required)
        else "failed"
    )
    return {
        "task_outcome": outcome,
        "checks": rows,
        "page_health": "not_checked"
        if not health
        else "passed"
        if all(r["status"] == "passed" for r in health)
        else "failed",
        "coverage_note": "No deterministic task assertion configured; page-health checks do not prove task success."
        if not required
        else "Task success is based on configured deterministic assertions.",
        "method": "Independent deterministic logic on the same browser run; no LLM self-rating.",
    }
