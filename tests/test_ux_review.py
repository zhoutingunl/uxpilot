import json

import httpx
import pytest
from test_llm import providers, sse

from ai_ux.agent import judge_run
from ai_ux.evidence import Evidence
from ai_ux.llm import LLM
from ai_ux.replay import render_replay
from ai_ux.report import aggregate, render
from ai_ux.schema import Judgement, Task, UXReview, UXSuggestion
from ai_ux.ux_review import review_context, validate_suggestions


def suggestion(**updates):
    return UXSuggestion.model_validate(
        {
            "category": "visual_hierarchy",
            "priority": "medium",
            "title": "突出表单的主要操作",
            "observations": ["提交和取消按钮具有相同的强调色"],
            "recommendation": "保留提交按钮的强调色，把取消改为次级按钮。",
            "expected_benefit": "可能减少选择主要操作所需的判断，需后续验证。",
            "acceptance": ["提交为主要按钮，取消为次级按钮；均可通过键盘操作。"],
            "evidence_refs": ["screenshots/step-000.png"],
            "rule_refs": ["primary-action"],
            "confidence": "high",
            **updates,
        }
    )


def fixture_evidence(tmp_path):
    evidence = Evidence(tmp_path / "run")
    evidence.event("initial")
    (evidence.root / "screenshots/step-000.png").write_bytes(b"screenshot")
    evidence.observations = [
        {
            "step": 0,
            "t_ms": 0,
            "url": "https://example.test/",
            "screenshot": "screenshots/step-000.png",
            "viewport": {"width": 390, "height": 844},
        }
    ]
    return evidence


async def test_native_video_receives_skill_and_returns_suggestions_in_one_call(tmp_path, monkeypatch):
    evidence = fixture_evidence(tmp_path)
    (evidence.root / "review.mp4").write_bytes(b"video")
    seen = []
    answer = Judgement(
        summary="任务可完成，主次操作可进一步区分。",
        task_assessment="passed",
        ux_suggestions=[suggestion(video_timestamps=["00:02"])],
    )

    def handle(request):
        payload = json.loads(request.content)
        parts = payload["messages"][1]["content"]
        context = json.loads(parts[0]["text"])
        assert context["ux_review"]["name"] == "ui-ux-pro-max"
        assert "primary-action" in {rule["id"] for rule in context["ux_review"]["rules"]}
        assert parts[1]["type"] == "video_url"
        assert "ux_suggestions" in payload["messages"][0]["content"]
        seen.append(payload)
        return sse(answer.model_dump_json())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        llm = LLM(providers(monkeypatch), client=client)
        result, _ = await judge_run(
            llm,
            evidence,
            Task(id="form", goal="Submit the form"),
            {},
            {"status": "ready", "review": {"path": "review.mp4", "duration_seconds": 4}},
        )
    assert len(seen) == 1
    assert result["issues"] == [] and len(result["ux_suggestions"]) == 1
    assert result["ux_suggestions"][0]["status"] == "needs_review"
    assert json.loads((evidence.root / "ux-review-context.json").read_text())["sha256"]
    assert json.loads((evidence.root / "ux-suggestions-rejected.json").read_text()) == []


@pytest.mark.parametrize(
    "updates,video,reason",
    [
        ({"rule_refs": ["imaginary-rule"]}, {}, "unknown_ux_rule"),
        ({"evidence_refs": ["../secret.png"]}, {}, "missing_or_nonconcrete_evidence"),
        ({"evidence_refs": ["ux-review-context.json"]}, {}, "missing_or_nonconcrete_evidence"),
        ({"evidence_refs": ["review.mp4"]}, {}, "missing_or_nonconcrete_evidence"),
        ({}, {"status": "ready"}, "missing_video_timestamp"),
        ({"video_timestamps": ["00:99"]}, {"status": "ready"}, "invalid_video_timestamp"),
        (
            {"video_timestamps": ["00:06"]},
            {"status": "ready", "duration_seconds": 5},
            "invalid_video_timestamp",
        ),
        (
            {"video_timestamps": ["00:04"]},
            {
                "status": "ready",
                "duration_seconds": 50,
                "review": {"duration_seconds": 3},
            },
            "invalid_video_timestamp",
        ),
        ({"video_timestamps": ["00:01"]}, {"status": "disabled"}, "invalid_video_timestamp"),
    ],
)
def test_rejects_unverifiable_rules_evidence_and_video_clock(tmp_path, updates, video, reason):
    evidence = fixture_evidence(tmp_path)
    judgement = Judgement(summary="Review", task_assessment="passed", ux_suggestions=[suggestion(**updates)])
    context = review_context(UXReview())
    evidence.write("ux-review-context.json", context)
    (evidence.root / "review.mp4").write_bytes(b"video")
    rejected = validate_suggestions(
        judgement,
        context,
        evidence,
        ["screenshots/step-000.png", "review.mp4", "ux-review-context.json"],
        video,
    )
    assert judgement.ux_suggestions == [] and rejected[0]["reason"] == reason


def test_disabled_and_limit_are_enforced_without_requiring_updated_model_output(tmp_path):
    evidence = fixture_evidence(tmp_path)
    judgement = Judgement(
        summary="Review",
        task_assessment="passed",
        ux_suggestions=[suggestion(), suggestion()],
    )
    refs = ["screenshots/step-000.png"]
    rejected = validate_suggestions(
        judgement, review_context(UXReview(max_suggestions=1)), evidence, refs, {}
    )
    assert len(judgement.ux_suggestions) == 1 and rejected[0]["reason"] == "suggestion_limit"
    rejected = validate_suggestions(judgement, review_context(UXReview(enabled=False)), evidence, refs, {})
    assert judgement.ux_suggestions == [] and rejected[0]["reason"] == "ux_review_disabled"
    assert Judgement.model_validate({"summary": "Legacy", "task_assessment": "passed"}).ux_suggestions == []


def test_report_and_replay_show_actionable_suggestions_separate_from_defects(tmp_path):
    item = suggestion(
        title="<script>unsafe</script>",
        recommendation="<img onerror=alert(1)>",
        evidence_refs=["screenshots/a b.png"],
        video_timestamps=["00:02"],
    ).model_dump()
    run = {
        "task_id": "form",
        "run_id": "form-01",
        "attempt": 1,
        "goal": "Submit",
        "stop_reason": "done",
        "duration_ms": 10,
        "judgement": {"summary": "Review", "limitations": [], "issues": [], "ux_suggestions": [item]},
    }
    assert aggregate([run]) == []
    render(tmp_path, {"batch_id": "test", "target_url": "https://example.test", "runs": [run], "issues": []})
    body = (tmp_path / "report.html").read_text()
    assert "UX 优化建议" in body and "primary-action" in body
    assert "<script>unsafe" not in body and "&lt;script&gt;unsafe" in body
    assert "form-01/screenshots/a%20b.png" in body
    assert "验收方式" in body and "00:02" in body
    markdown = (tmp_path / "report.md").read_text()
    assert "## UX 优化建议" in markdown and "预期收益（待验证）" in markdown
    assert "form-01/ux-review-context.json" in markdown
    render_replay(tmp_path, run)
    assert "UX 优化建议" in (tmp_path / "replay.html").read_text()
    assert json.loads((tmp_path / "report.json").read_text())["runs"][0]["judgement"]["ux_suggestions"]
