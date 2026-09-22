import json
from unittest.mock import Mock

import pytest

from app.services import resume_llm
from app.services.llm_client import LlmError


def prep(resume="Python developer", job="Python required"):
    return resume_llm.generate_interview_prep_with_llm(
        resume, job, provider="openai", model="test-model", api_key="test-key", base_url=None
    )


def test_prep_prompt_formats_inputs_and_preserves_response_contract(monkeypatch):
    response = {
        "likely_questions": [
            {"question": "Tell me about a time you debugged a hard issue.", "category": "behavioral", "approach": "Lean on the outage story."},
        ],
        "talking_points": ["Led the API migration"],
        "questions_to_ask": ["What does success look like in the first 90 days?"],
    }
    call = Mock(return_value=json.dumps(response))
    monkeypatch.setattr(resume_llm, "call_llm", call)
    resume = 'Built APIs returning {"status": "ok"}.\nExperience with Python.'
    job = "Requires Python and SQL. Example: {job_description}"

    result = prep(resume, job)

    prompt = call.call_args.kwargs["prompt"]
    assert resume in prompt
    assert job in prompt
    assert '"likely_questions"' in prompt
    assert "{{" not in prompt  # template JSON braces must be escaped for .format()
    assert call.call_args.kwargs | {"prompt": None} == {
        "provider": "openai", "model": "test-model", "api_key": "test-key",
        "base_url": None, "prompt": None,
    }
    assert result.likely_questions == [
        {
            "question": "Tell me about a time you debugged a hard issue.",
            "category": "behavioral",
            "approach": "Lean on the outage story.",
        }
    ]
    assert result.talking_points == response["talking_points"]
    assert result.questions_to_ask == response["questions_to_ask"]
    assert result.raw_response == response


def test_prep_prompt_limits_each_input_independently(monkeypatch):
    call = Mock(return_value='{"likely_questions": []}')
    monkeypatch.setattr(resume_llm, "call_llm", call)
    limit = resume_llm._MAX_TEXT_CHARS
    prep("R" * limit + "RESUME_OVERFLOW", "J" * limit + "JOB_OVERFLOW")
    prompt = call.call_args.kwargs["prompt"]
    assert "R" * limit in prompt and "J" * limit in prompt
    assert "RESUME_OVERFLOW" not in prompt and "JOB_OVERFLOW" not in prompt


def test_prep_drops_a_question_missing_its_own_text(monkeypatch):
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: json.dumps({
        "likely_questions": [
            {"question": "Real question", "category": "technical", "approach": "Answer it."},
            {"category": "behavioral", "approach": "No question text — dropped."},
            "not even a dict",
        ],
    }))
    result = prep()
    assert result.likely_questions == [
        {"question": "Real question", "category": "technical", "approach": "Answer it."}
    ]


def test_prep_falls_back_to_role_specific_for_an_unrecognized_category(monkeypatch):
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: json.dumps({
        "likely_questions": [{"question": "Q", "category": "made up", "approach": ""}],
    }))
    result = prep()
    assert result.likely_questions[0]["category"] == "role_specific"


@pytest.mark.parametrize("response", ["not json", "[]"])
def test_prep_rejects_invalid_model_response(monkeypatch, response):
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: response)
    with pytest.raises(LlmError):
        prep()
