import threading

import pytest

from app.services import llm_client
from app.services.llm_client import LlmError, call_llm

_ARGS = {"provider": "deepseek", "model": "m", "api_key": "k", "base_url": None, "prompt": "p"}


def test_call_past_its_deadline_raises_instead_of_hanging(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(llm_client, "_call", lambda **kwargs: release.wait(5) and "late")

    with pytest.raises(LlmError, match="took too long"):
        call_llm(**_ARGS, deadline_seconds=0.1)
    release.set()


def test_call_within_its_deadline_returns_the_text(monkeypatch):
    monkeypatch.setattr(llm_client, "_call", lambda **kwargs: "{}")

    assert call_llm(**_ARGS, deadline_seconds=5) == "{}"


def test_call_error_within_its_deadline_is_raised_as_is(monkeypatch):
    def fail(**kwargs):
        raise LlmError("deepseek returned no content.")

    monkeypatch.setattr(llm_client, "_call", fail)

    with pytest.raises(LlmError, match="no content"):
        call_llm(**_ARGS, deadline_seconds=5)
