from __future__ import annotations

import unicodedata


def normalize_speech(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())


def completed_actions(events):
    return [
        e
        for e in events
        if e["kind"] == "product_action"
        and e["data"].get("kind") == "result"
        and e["data"].get("status") == "completed"
    ]


def check_events(assertion, events):
    input_index = next(
        (i for i in range(len(events) - 1, -1, -1) if events[i]["kind"] == "audio_input_start"), None
    )
    turn_events = events[input_index:] if input_index is not None else events
    if assertion.kind == "response_speech":
        return any(
            e["kind"] == "response_transcript"
            and e.get("status") == "completed"
            and normalize_speech(assertion.contains) in normalize_speech(e.get("transcript", ""))
            for e in turn_events
        )
    if assertion.kind == "voice_session_language":
        connections = [
            e["data"]
            for e in events
            if e["kind"] == "voice_event"
            and e.get("direction") == "sent"
            and e["data"].get("type") == "connect"
        ]
        return bool(connections) and connections[-1].get("language") == assertion.contains
    if assertion.kind == "action_completed":
        return any(
            e["data"].get("text", "").split(" · ")[0] == assertion.action_name
            for e in completed_actions(turn_events)
        )
    if assertion.kind == "voice_transcript":
        return any(
            e["kind"] == "voice_event"
            and e.get("direction") == "received"
            and e["data"].get("type") == "transcript"
            and (not assertion.role or e["data"].get("role") == assertion.role)
            and (not assertion.source or e["data"].get("source", "cloud") == assertion.source)
            and (not assertion.contains or assertion.contains in e["data"].get("text", ""))
            for e in turn_events
        )
    if assertion.kind == "audio_received":
        return any(e["kind"] == "first_audio" for e in turn_events)
    if assertion.kind == "voice_turn_completed":
        return any(
            e["kind"] == "voice_event"
            and e.get("direction") == "received"
            and (
                (e["data"].get("type") == "playback_end" and e["data"].get("aborted") is False)
                or (
                    e["data"].get("type") == "voice_milestone"
                    and e["data"].get("phase") == "turn_end"
                    and e["data"].get("result") == "completed"
                )
            )
            for e in turn_events
        )
    if assertion.kind == "audio_played":
        # A backend TTS completion alone does not establish browser playback.
        # Compare counters with the last pre-input snapshot to exclude greetings.
        if input_index is None or not any(e["kind"] == "first_audio" for e in turn_events):
            return False
        snapshots = [
            e["data"]
            for e in events[:input_index]
            if e["kind"] == "voice_event"
            and e.get("direction") == "sent"
            and e["data"].get("type") == "playback_status"
        ]
        baseline = snapshots[-1] if snapshots else {}
        for e in reversed(turn_events):
            data = e.get("data", {})
            if (
                e["kind"] != "voice_event"
                or e.get("direction") != "sent"
                or data.get("type") != "playback_status"
            ):
                continue
            return (
                data.get("output_state") == "running"
                and data.get("received_frames", 0) > baseline.get("received_frames", 0)
                and data.get("received_frames") == data.get("scheduled_frames") == data.get("ended_frames")
                and data.get("dropped_frames") == baseline.get("dropped_frames", 0)
                and data.get("queued_ms") == 0
                and data.get("signal_peak_milli", 0) > 0
            )
        return False
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
