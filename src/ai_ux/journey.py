"""Bounded multi-phase exploration in one browser context, with immutable checkpoints."""

import json

from .schema import Action, Phase
from .verifier import verify


def combine_verification(final, phases, invariants):
    result = dict(final)
    result["phases"] = phases
    result["invariant_checks"] = invariants
    outcomes = [final["task_outcome"]] + [p["verification"]["task_outcome"] for p in phases]
    required_failures = any(c["required"] and c["status"] != "passed" for c in invariants)
    if "failed" in outcomes or required_failures:
        result["task_outcome"] = "failed"
    elif any(p["status"] not in {"explorer_done", "checkpoint_reached"} for p in phases):
        result["task_outcome"] = "inconclusive"
    elif phases:
        result["task_outcome"] = (
            "passed" if all(p["verification"]["task_outcome"] == "passed" for p in phases) else "not_verified"
        )
    recovery = [p["verification"]["task_outcome"] for p in phases if p["role"] in {"recovery", "retry"}]
    if recovery:
        result["recovery_outcome"] = (
            "failed"
            if "failed" in recovery
            else "passed"
            if all(o == "passed" for o in recovery)
            else "not_verified"
        )
    return result


async def explore(llm, runtime, task, evidence, system, policy, *, phases, invariants, log):
    """Explorer sees goals and observations; verification rules stay private."""
    sequence = task.phases or [Phase(id="task", goal=task.goal, max_steps=min(task.max_steps, 20))]
    history, used = [], 0
    setup_failed = False

    async def check_invariants(observation, phase_id):
        if task.invariants:
            checkpoint = await verify(runtime, task.invariants)
            invariants.extend(
                {
                    **check,
                    "phase_id": phase_id,
                    "t_ms": evidence.ms(),
                    "observation": observation.get("evidence_ref", observation["screenshot"]),
                }
                for check in checkpoint["checks"]
            )
            evidence.write("invariant-checks.json", invariants)

    for phase in sequence:
        start_event = len(evidence.events)
        start_ms = evidence.ms()
        record = {
            "id": phase.id,
            "role": phase.role,
            "goal": phase.goal,
            "started_ms": start_ms,
            "status": "running",
            "observations": [],
            "visited_urls": [],
            "verification": {"task_outcome": "not_verified", "checks": []},
        }
        if task.phases:
            phases.append(record)
        if used >= task.max_steps or (setup_failed and phase.role == "transition"):
            record.update(
                status="blocked",
                ended_ms=evidence.ms(),
                reason="task_budget_exhausted" if used >= task.max_steps else "setup_failed",
            )
            evidence.write(f"phases/{phase.id}.json", record)
            continue
        evidence.event("phase_start", phase_id=phase.id, role=phase.role, goal=phase.goal)
        errors = 0
        status = "phase_step_limit" if task.phases else "step_limit"
        budget = min(phase.max_steps if task.phases else task.max_steps, task.max_steps - used)
        for _ in range(budget):
            observation = await runtime.observe(len(evidence.observations))
            if observation["url"] not in record["visited_urls"]:
                record["visited_urls"].append(observation["url"])
            record["observations"].append(observation.get("evidence_ref", observation["screenshot"]))
            await check_invariants(observation, phase.id)
            if task.phases and phase.auto_advance:
                current = await verify(runtime, phase.assertions, events=evidence.events[start_event:])
                if current["task_outcome"] == "passed":
                    record["verification"] = current
                    status = "checkpoint_reached"
                    break
            data = {
                "task": task.public(),
                "policy": policy,
                "observation": observation,
                "recent_actions": history[-7:],
                "remaining_steps": task.max_steps - used,
            }
            if task.phases:
                data["current_phase"] = {"id": phase.id, "goal": phase.goal, "role": phase.role}
                data["journey"] = [{"id": p.id, "role": p.role, "goal": p.goal} for p in task.phases]
                data["phase_history"] = [
                    {"id": p["id"], "goal": p["goal"], "visited_urls": p.get("visited_urls", [])}
                    for p in phases
                    if p is not record
                ]
                pages = {}
                for item in evidence.observations:
                    pages[item["url"]] = {"url": item["url"], "title": item.get("title", "")}
                data["visited_pages"] = list(pages.values())[-50:]
            log(
                f"[{evidence.root.name}] step {used + 1}/{task.max_steps}: observing"
                + (f" · {phase.id}" if task.phases else ""),
                flush=True,
            )
            decision, call = await llm.structured(
                system,
                json.dumps(data, ensure_ascii=False),
                Action,
                images=[evidence.resolve(observation["screenshot"])],
                purpose="explore",
            )
            evidence.write(f"decisions/step-{used:03}.json", {"action": decision.model_dump(), "model": call})
            log(
                f"[{evidence.root.name}] {decision.kind} {decision.target or ''}: {decision.rationale}",
                flush=True,
            )
            used += 1
            if decision.kind == "done":
                status = "explorer_done"
                break
            response = await runtime.perform(decision)
            evidence.event(
                "agent_action_result",
                step=used - 1,
                phase_id=phase.id,
                action_kind=decision.kind,
                target=decision.target,
                **response,
            )
            history.append({"action": decision.model_dump(), "result": response})
            errors = errors + 1 if not response["ok"] else 0
            if errors >= 3:
                status = "consecutive_action_errors"
                break
        observation = await runtime.observe(len(evidence.observations))
        if observation["url"] not in record["visited_urls"]:
            record["visited_urls"].append(observation["url"])
        await check_invariants(observation, phase.id)
        record["observations"].append(observation.get("evidence_ref", observation["screenshot"]))
        record.update(status=status, ended_ms=evidence.ms())
        if task.phases:
            if status != "checkpoint_reached":
                record["verification"] = await verify(
                    runtime, phase.assertions, events=evidence.events[start_event:]
                )
            evidence.write(f"phases/{phase.id}.json", record)
            evidence.write("phase-results.json", phases)
            evidence.event("phase_end", phase_id=phase.id, status=status, verification=record["verification"])
            if phase.role == "setup" and record["verification"]["task_outcome"] == "failed":
                setup_failed = True
        else:
            return status
    return (
        "journey_completed"
        if all(p["status"] in {"explorer_done", "checkpoint_reached"} for p in phases)
        else "journey_incomplete"
    )
