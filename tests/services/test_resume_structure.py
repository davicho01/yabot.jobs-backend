import json
from unittest.mock import Mock

from app.services import resume_llm


def structure(resume_text="Python developer with 5 years experience."):
    return resume_llm.extract_resume_structure_with_llm(
        resume_text, provider="openai", model="test-model", api_key="test-key", base_url=None
    )


def test_structure_prompt_includes_the_resume_text(monkeypatch):
    call = Mock(return_value=json.dumps({"summary": "", "sections": []}))
    monkeypatch.setattr(resume_llm, "call_llm", call)
    structure(resume_text="Built APIs at Acme Corp.")
    prompt = call.call_args.kwargs["prompt"]
    assert "Built APIs at Acme Corp." in prompt


def test_structure_prompt_has_no_job_description_section(monkeypatch):
    # Unlike TAILOR_PROMPT, this is a pure structuring pass — no job to
    # target the resume at, so there's no "Job description:" section at all.
    call = Mock(return_value=json.dumps({"summary": "", "sections": []}))
    monkeypatch.setattr(resume_llm, "call_llm", call)
    structure()
    prompt = call.call_args.kwargs["prompt"]
    assert "Job description:" not in prompt
    assert "never invent" in prompt.lower()  # guards against embellishment


def test_structure_parses_summary_sections_and_contact(monkeypatch):
    raw = json.dumps(
        {
            "contact": {"name": "Jane Doe", "email": "jane@example.com"},
            "summary": "Backend engineer.",
            "sections": [
                {
                    "heading": "Experience",
                    "bullets": [],
                    "entries": [{"title": "Engineer", "subtitle": "Acme Corp", "bullets": ["Built APIs"]}],
                }
            ],
        }
    )
    monkeypatch.setattr(resume_llm, "call_llm", Mock(return_value=raw))

    result = structure()

    assert result.summary == "Backend engineer."
    assert result.contact == {"name": "Jane Doe", "email": "jane@example.com"}
    assert result.sections[0]["heading"] == "Experience"
    assert result.sections[0]["entries"][0]["title"] == "Engineer"
