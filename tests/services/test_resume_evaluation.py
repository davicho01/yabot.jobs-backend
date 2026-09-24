import json
from unittest.mock import Mock

import pytest

from app.services import resume_llm
from app.services.llm_client import LlmError


def evaluate(overall_score, resume="Python developer", job="Python required"):
    return resume_llm.evaluate_resume_with_llm(
        resume, job, overall_score, provider="openai", model="test-model", api_key="test-key", base_url=None
    )


def test_evaluation_prompt_formats_inputs_and_preserves_response_contract(monkeypatch):
    response = {
        # Already sums to overall_score (79) — no reconciliation needed here,
        # that's covered separately below.
        "category_scores": [
            {
                "category": "required_skills", "score": 45, "why": "Strong Python background.",
                "job_requirements": ["Python"], "strengths": ["Built Python APIs"], "weaknesses": [],
            },
            {
                "category": "responsibilities", "score": 22, "why": "Shipped comparable services.",
                "job_requirements": ["API ownership"], "strengths": ["Owned API delivery"], "weaknesses": [],
            },
            {
                "category": "seniority", "score": 10, "why": "Scope roughly matches.",
                "job_requirements": [], "strengths": [], "weaknesses": [],
            },
            {
                "category": "preferred_qualifications", "score": 2, "why": "SQL not demonstrated.",
                "job_requirements": ["SQL"], "strengths": [], "weaknesses": ["No SQL evidence"],
            },
        ],
    }
    call = Mock(return_value=json.dumps(response))
    monkeypatch.setattr(resume_llm, "call_llm", call)
    resume = 'Built APIs returning {"status": "ok"}.\nExperience with Python.'
    job = 'Requires Python and SQL. Example: {job_description}'
    result = evaluate(79, resume, job)
    prompt = call.call_args.kwargs["prompt"]
    assert resume in prompt
    assert job in prompt
    assert "79" in prompt  # the pinned overall_score is embedded in the prompt
    assert '"category_scores"' in prompt
    assert '{{' not in prompt  # template JSON braces must be escaped for .format()
    assert call.call_args.kwargs | {"prompt": None} == {
        "provider": "openai", "model": "test-model", "api_key": "test-key",
        "base_url": None, "prompt": None,
    }
    assert result.category_scores == [
        {
            "category": "required_skills", "score": 45, "max_score": 50, "why": "Strong Python background.",
            "job_requirements": ["Python"], "strengths": ["Built Python APIs"], "weaknesses": [],
        },
        {
            "category": "responsibilities", "score": 22, "max_score": 30, "why": "Shipped comparable services.",
            "job_requirements": ["API ownership"], "strengths": ["Owned API delivery"], "weaknesses": [],
        },
        {
            "category": "seniority", "score": 10, "max_score": 15, "why": "Scope roughly matches.",
            "job_requirements": [], "strengths": [], "weaknesses": [],
        },
        {
            "category": "preferred_qualifications", "score": 2, "max_score": 5, "why": "SQL not demonstrated.",
            "job_requirements": ["SQL"], "strengths": [], "weaknesses": ["No SQL evidence"],
        },
    ]
    assert result.raw_response == response


def test_evaluation_category_breakdown_reconciles_shortfall_to_overall_score(monkeypatch):
    # The pinned overall_score (82) is authoritative, but the model's own
    # category scores only sum to 50 — code must add the missing 32 points
    # onto categories (in rubric order) that still have headroom under
    # their own cap.
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: json.dumps({
        "category_scores": [
            {"category": "required_skills", "score": 50},  # already at its cap, no headroom
            {"category": "responsibilities", "score": 0},
        ],
    }))
    result = evaluate(82)
    by_category = {c["category"]: c["score"] for c in result.category_scores}
    assert by_category == {
        "required_skills": 50, "responsibilities": 30, "seniority": 2, "preferred_qualifications": 0,
    }
    assert sum(by_category.values()) == 82


def test_evaluation_category_breakdown_reconciles_excess_over_overall_score(monkeypatch):
    # Category scores overshoot overall_score (82 vs a sum of 100) — code
    # must trim the excess 18 points off categories in rubric order.
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: json.dumps({
        "category_scores": [
            {"category": "required_skills", "score": 50},
            {"category": "responsibilities", "score": 30},
            {"category": "seniority", "score": 15},
            {"category": "preferred_qualifications", "score": 5},
        ],
    }))
    result = evaluate(82)
    by_category = {c["category"]: c["score"] for c in result.category_scores}
    assert by_category == {
        "required_skills": 32, "responsibilities": 30, "seniority": 15, "preferred_qualifications": 5,
    }
    assert sum(by_category.values()) == 82


def test_evaluation_category_breakdown_is_bounded_by_fixed_caps(monkeypatch):
    # Model returns categories out of order, one missing entirely, one with a
    # score above its cap and a bogus max_score, and an unknown category —
    # code must still produce exactly 4 entries in canonical order, each
    # clamped to its real fixed cap regardless of what the model sent.
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: json.dumps({
        "category_scores": [
            {"category": "preferred_qualifications", "score": 999, "max_score": 1},
            {"category": "required_skills", "score": -10},
            {"category": "unknown_category", "score": 100},
        ],
    }))
    result = evaluate(5)
    assert [c["category"] for c in result.category_scores] == [
        "required_skills", "responsibilities", "seniority", "preferred_qualifications",
    ]
    by_category = {c["category"]: c for c in result.category_scores}
    assert by_category["responsibilities"] == {
        "category": "responsibilities", "score": 0, "max_score": 30, "why": "",
        "job_requirements": [], "strengths": [], "weaknesses": [],
    }
    assert by_category["preferred_qualifications"]["score"] == 5  # clamped to the real max, not the model's
    assert by_category["preferred_qualifications"]["max_score"] == 5
    assert sum(c["score"] for c in result.category_scores) == 5


@pytest.mark.parametrize("response", ["not json", "[]"])
def test_evaluation_rejects_invalid_model_response(monkeypatch, response):
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: response)
    with pytest.raises(LlmError):
        evaluate(50)
