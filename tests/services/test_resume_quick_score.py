import json
from unittest.mock import Mock

import pytest

from app.services import resume_llm
from app.services.llm_client import LlmError


def score(resume="Python developer", job="Python required"):
    return resume_llm.quick_score_resume_with_llm(
        resume, job, provider="openai", model="test-model", api_key="test-key", base_url=None
    )


def test_quick_score_prompt_formats_inputs_and_preserves_response_contract(monkeypatch):
    response = {
        "overall_score": 79,
        "matched_keywords": ["Python", "APIs"],
        "missing_keywords": ["SQL"],
        "summary": "Built Python APIs. SQL experience is not demonstrated.",
        "overqualification_note": "",
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
    assert result.raw_response == response


def test_quick_score_prompt_limits_each_input_independently(monkeypatch):
    call = Mock(return_value='{"overall_score": 0}')
    monkeypatch.setattr(resume_llm, "call_llm", call)
    limit = resume_llm._MAX_TEXT_CHARS
    score("R" * limit + "RESUME_OVERFLOW", "J" * limit + "JOB_OVERFLOW")
    prompt = call.call_args.kwargs["prompt"]
    assert "R" * limit in prompt and "J" * limit in prompt
    assert "RESUME_OVERFLOW" not in prompt and "JOB_OVERFLOW" not in prompt


def test_quick_score_normalizes_non_score_model_output(monkeypatch):
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: json.dumps({
        "matched_keywords": ["Python", None, 7],
        "missing_keywords": "SQL", "summary": None,
    }))
    result = score()
    assert result.matched_keywords == ["Python"]
    assert result.missing_keywords == []
    assert result.summary == ""
    assert result.overqualification_note == ""  # defaults to empty, doesn't blow up on missing key


def test_quick_score_passes_through_overqualification_note(monkeypatch):
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: json.dumps({
        "overall_score": 90,
        "overqualification_note": "15+ years vs. a role scoped for 2-4 — may read as overqualified.",
    }))
    result = score()
    assert result.overqualification_note == "15+ years vs. a role scoped for 2-4 — may read as overqualified."
    assert result.overall_score == 90  # unaffected by the note


@pytest.mark.parametrize("raw,expected", [(30, 30), (125, 100), (-5, 0), ("82", 82), (None, 0), ("unknown", 0)])
def test_quick_score_normalizes_overall_score_from_model_output(monkeypatch, raw, expected):
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: json.dumps({"overall_score": raw}))
    result = score()
    assert result.overall_score == expected


@pytest.mark.parametrize("response", ["not json", "[]"])
def test_quick_score_rejects_invalid_model_response(monkeypatch, response):
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: response)
    with pytest.raises(LlmError):
        score()
