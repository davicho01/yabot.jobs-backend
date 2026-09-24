import json
from unittest.mock import Mock

import pytest

from app.services import resume_llm
from app.services.llm_client import LlmError


def roles(resume="Senior Engineer at Acme (2020-Present)"):
    return resume_llm.extract_resume_roles_with_llm(
        resume, provider="openai", model="test-model", api_key="test-key", base_url=None
    )


def apply(additions, resume="Senior Engineer at Acme (2020-Present)"):
    return resume_llm.apply_skill_additions_with_llm(
        resume, additions, provider="openai", model="test-model", api_key="test-key", base_url=None
    )


def test_extract_resume_roles_formats_input_and_preserves_response(monkeypatch):
    response = {"roles": ["Senior Backend Engineer — Acme Corp (2020–Present)", "Engineer — Globex (2017–2020)"]}
    call = Mock(return_value=json.dumps(response))
    monkeypatch.setattr(resume_llm, "call_llm", call)

    result = roles("Built APIs at Acme.")

    prompt = call.call_args.kwargs["prompt"]
    assert "Built APIs at Acme." in prompt
    assert '"roles"' in prompt
    assert result.roles == response["roles"]
    assert result.raw_response == response


def test_extract_resume_roles_limits_input(monkeypatch):
    call = Mock(return_value='{"roles": []}')
    monkeypatch.setattr(resume_llm, "call_llm", call)
    limit = resume_llm._MAX_TEXT_CHARS

    roles("R" * limit + "OVERFLOW")

    prompt = call.call_args.kwargs["prompt"]
    assert "R" * limit in prompt
    assert "OVERFLOW" not in prompt


def test_extract_resume_roles_rejects_invalid_model_response(monkeypatch):
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: "not json")
    with pytest.raises(LlmError):
        roles()


def test_apply_skill_additions_formats_resume_and_additions_into_prompt(monkeypatch):
    call = Mock(return_value=json.dumps({"summary": "", "sections": []}))
    monkeypatch.setattr(resume_llm, "call_llm", call)

    apply(
        [
            {
                "keyword": "Kubernetes",
                "target_role": "Senior Backend Engineer — Acme Corp",
                "explanation": "Ran our k8s clusters in prod for two years.",
            },
            {
                "keyword": "GraphQL",
                "target_role": "General / Skills section",
                "explanation": "Built a few internal GraphQL APIs.",
            },
        ],
        resume="Senior Backend Engineer at Acme Corp.",
    )

    prompt = call.call_args.kwargs["prompt"]
    assert "Senior Backend Engineer at Acme Corp." in prompt
    assert "Skill: Kubernetes" in prompt
    assert "Belongs under: Senior Backend Engineer — Acme Corp" in prompt
    assert "Candidate's explanation: Ran our k8s clusters in prod for two years." in prompt
    assert "Skill: GraphQL" in prompt
    assert "Belongs under: General / Skills section" in prompt


def test_apply_skill_additions_preserves_the_tailored_content_contract(monkeypatch):
    response = {
        "contact": {"name": "Jane Doe", "email": "jane@example.com"},
        "summary": "Backend engineer.",
        "sections": [
            {
                "heading": "Experience",
                "bullets": [],
                "entries": [
                    {
                        "title": "Senior Backend Engineer",
                        "subtitle": "Acme Corp",
                        "bullets": ["Shipped APIs.", "Ran our Kubernetes clusters in production."],
                    }
                ],
            }
        ],
    }
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: json.dumps(response))

    result = apply([{"keyword": "Kubernetes", "target_role": "Senior Backend Engineer", "explanation": "..."}])

    assert result.summary == "Backend engineer."
    assert result.contact == {"name": "Jane Doe", "email": "jane@example.com"}
    assert result.sections[0]["entries"][0]["bullets"] == [
        "Shipped APIs.",
        "Ran our Kubernetes clusters in production.",
    ]
    assert result.raw_response == response


def test_apply_skill_additions_rejects_invalid_model_response(monkeypatch):
    monkeypatch.setattr(resume_llm, "call_llm", lambda **_: "not json")
    with pytest.raises(LlmError):
        apply([{"keyword": "SQL", "target_role": "General / Skills section", "explanation": "..."}])
