import json
from unittest.mock import Mock

import pytest

from app.services import resume_llm
from app.services.llm_client import LlmError


def score(resume="Python developer", job="Python required"):
    return resume_llm.score_resume_with_llm(
        resume, job, provider="openai", model="test-model", api_key="test-key", base_url=None
    )


def test_score_prompt_formats_inputs_and_preserves_response_contract(monkeypatch):
    response = {
        "overall_score": 79,
        "matched_keywords": ["Python", "APIs"],
        "missing_keywords": ["SQL"],
        "summary": "Built Python APIs. SQL experience is not demonstrated.",
        "overqualification_note": "",
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
    result = score(resume, job)
    prompt = call.call_args.kwargs["prompt"]
    assert resume in prompt
    assert job in prompt
    assert '"overall_score"' in prompt
    assert '{{' not in prompt  # template JSON braces must be escaped for .format()
    assert call.call_args.kwargs | {"prompt": None} == {
        "provider": "openai", "model": "test-model", "api_key": "test-key",
        "base_url": None, "prompt": None,
    }
    assert result.overall_score == 79  # trusted directly from the model
    assert result.matched_keywords == response["matched_keywords"]
    assert result.missing_keywords == response["missing_keywords"]
    assert result.summary == response["summary"]
    assert result.overqualification_note == ""
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


def test_score_category_breakdown_reconciles_shortfall_to_overall_score(monkeypatch):
    # Model's own overall_score (82) is trusted, but its category scores only
    # sum to 50 — code must add the missing 32 points onto categories (in
    # rubric order) that still have headroom under their own cap.
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: json.dumps({
        "overall_score": 82,
        "category_scores": [
            {"category": "required_skills", "score": 50},  # already at its cap, no headroom
            {"category": "responsibilities", "score": 0},
        ],
    }))
    result = score()
    assert result.overall_score == 82
    by_category = {c["category"]: c["score"] for c in result.category_scores}
    assert by_category == {
        "required_skills": 50, "responsibilities": 30, "seniority": 2, "preferred_qualifications": 0,
    }
    assert sum(by_category.values()) == 82


def test_score_category_breakdown_reconciles_excess_over_overall_score(monkeypatch):
    # Category scores overshoot overall_score (82 vs a sum of 100) — code
    # must trim the excess 18 points off categories in rubric order.
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: json.dumps({
        "overall_score": 82,
        "category_scores": [
            {"category": "required_skills", "score": 50},
            {"category": "responsibilities", "score": 30},
            {"category": "seniority", "score": 15},
            {"category": "preferred_qualifications", "score": 5},
        ],
    }))
    result = score()
    assert result.overall_score == 82
    by_category = {c["category"]: c["score"] for c in result.category_scores}
    assert by_category == {
        "required_skills": 32, "responsibilities": 30, "seniority": 15, "preferred_qualifications": 5,
    }
    assert sum(by_category.values()) == 82


def test_score_category_breakdown_is_bounded_by_fixed_caps(monkeypatch):
    # Model returns categories out of order, one missing entirely, one with a
    # score above its cap and a bogus max_score, and an unknown category —
    # code must still produce exactly 4 entries in canonical order, each
    # clamped to its real fixed cap regardless of what the model sent.
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: json.dumps({
        "overall_score": 5,
        "category_scores": [
            {"category": "preferred_qualifications", "score": 999, "max_score": 1},
            {"category": "required_skills", "score": -10},
            {"category": "unknown_category", "score": 100},
        ],
    }))
    result = score()
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


def test_score_prompt_limits_each_input_independently(monkeypatch):
    call = Mock(return_value='{"overall_score": 0}')
    monkeypatch.setattr(resume_llm, "call_llm", call)
    limit = resume_llm._MAX_TEXT_CHARS
    score("R" * limit + "RESUME_OVERFLOW", "J" * limit + "JOB_OVERFLOW")
    prompt = call.call_args.kwargs["prompt"]
    assert "R" * limit in prompt and "J" * limit in prompt
    assert "RESUME_OVERFLOW" not in prompt and "JOB_OVERFLOW" not in prompt


def test_score_normalizes_non_score_model_output(monkeypatch):
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: json.dumps({
        "matched_keywords": ["Python", None, 7],
        "missing_keywords": "SQL", "summary": None,
    }))
    result = score()
    assert result.matched_keywords == ["Python"]
    assert result.missing_keywords == []
    assert result.summary == ""
    assert result.overqualification_note == ""  # defaults to empty, doesn't blow up on missing key


def test_score_passes_through_overqualification_note(monkeypatch):
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: json.dumps({
        "overall_score": 90,
        "overqualification_note": "15+ years vs. a role scoped for 2-4 — may read as overqualified.",
    }))
    result = score()
    assert result.overqualification_note == "15+ years vs. a role scoped for 2-4 — may read as overqualified."
    assert result.overall_score == 90  # unaffected by the note


@pytest.mark.parametrize("raw,expected", [(30, 30), (125, 100), (-5, 0), ("82", 82), (None, 0), ("unknown", 0)])
def test_score_normalizes_overall_score_from_model_output(monkeypatch, raw, expected):
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: json.dumps({"overall_score": raw}))
    result = score()
    assert result.overall_score == expected
    assert sum(c["score"] for c in result.category_scores) == expected  # breakdown reconciles to match


@pytest.mark.parametrize("response", ["not json", "[]"])
def test_score_rejects_invalid_model_response(monkeypatch, response):
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: response)
    with pytest.raises(LlmError):
        score()
