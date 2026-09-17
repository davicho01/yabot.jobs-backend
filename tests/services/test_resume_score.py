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
        "overall_score": 82,
        "matched_keywords": ["Python", "APIs"],
        "missing_keywords": ["SQL"],
        "summary": "Built Python APIs. SQL experience is not demonstrated.",
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
    assert result.overall_score == response["overall_score"]
    assert result.matched_keywords == response["matched_keywords"]
    assert result.missing_keywords == response["missing_keywords"]
    assert result.summary == response["summary"]
    assert result.raw_response == response


def test_score_prompt_limits_each_input_independently(monkeypatch):
    call = Mock(return_value='{"overall_score": 0}')
    monkeypatch.setattr(resume_llm, "call_llm", call)
    limit = resume_llm._MAX_TEXT_CHARS
    score("R" * limit + "RESUME_OVERFLOW", "J" * limit + "JOB_OVERFLOW")
    prompt = call.call_args.kwargs["prompt"]
    assert "R" * limit in prompt and "J" * limit in prompt
    assert "RESUME_OVERFLOW" not in prompt and "JOB_OVERFLOW" not in prompt


@pytest.mark.parametrize("raw,expected", [(125, 100), (-5, 0), ("82", 82), (None, 0), ("unknown", 0)])
def test_score_normalizes_model_output(monkeypatch, raw, expected):
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: json.dumps({
        "overall_score": raw, "matched_keywords": ["Python", None, 7],
        "missing_keywords": "SQL", "summary": None,
    }))
    result = score()
    assert result.overall_score == expected
    assert result.matched_keywords == ["Python"]
    assert result.missing_keywords == []
    assert result.summary == ""


@pytest.mark.parametrize("response", ["not json", "[]"])
def test_score_rejects_invalid_model_response(monkeypatch, response):
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: response)
    with pytest.raises(LlmError):
        score()
