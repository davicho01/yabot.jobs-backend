import pytest

from app.services import resume_llm
from app.services.llm_client import LlmError

_CREDS = {"provider": "openai", "model": "m", "api_key": "k", "base_url": None}


def _fake_replies(monkeypatch, replies):
    calls = []

    def call_llm(**kwargs):
        calls.append(kwargs)
        return replies[len(calls) - 1]

    monkeypatch.setattr(resume_llm, "call_llm", call_llm)
    return calls


def test_json_wrapped_in_prose_parses_without_a_retry(monkeypatch):
    calls = _fake_replies(monkeypatch, ['Here is the evaluation:\n{"overall_score": 80}\nHope that helps!'])

    assert resume_llm._call_llm_json(prompt="p", **_CREDS) == {"overall_score": 80}
    assert len(calls) == 1


def test_bad_format_is_retried_until_it_parses(monkeypatch):
    calls = _fake_replies(monkeypatch, ["not json at all", '["a list"]', '{"overall_score": 80}'])

    assert resume_llm._call_llm_json(prompt="p", **_CREDS) == {"overall_score": 80}
    assert len(calls) == 3


def test_gives_up_with_llm_error_after_the_last_try(monkeypatch):
    calls = _fake_replies(monkeypatch, ["nope"] * resume_llm._FORMAT_ATTEMPTS)

    with pytest.raises(LlmError, match="not valid JSON"):
        resume_llm._call_llm_json(prompt="p", **_CREDS)
    assert len(calls) == resume_llm._FORMAT_ATTEMPTS


def test_quick_score_goes_through_the_retry(monkeypatch):
    _fake_replies(monkeypatch, ["oops", '{"overall_score": 91, "summary": "Great fit."}'])

    result = resume_llm.quick_score_resume_with_llm("resume", "job", **_CREDS)

    assert result.overall_score == 91
    assert result.summary == "Great fit."
