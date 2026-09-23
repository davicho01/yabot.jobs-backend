import json
from unittest.mock import Mock

from app.services import resume_llm


def tailor(fitness_score=None, resume="Python developer", job="Python required"):
    return resume_llm.generate_tailored_resume_with_llm(
        resume, job, provider="openai", model="test-model", api_key="test-key", base_url=None,
        fitness_score=fitness_score,
    )


def test_tailor_prompt_notes_no_fitness_assessment_when_none_given(monkeypatch):
    call = Mock(return_value=json.dumps({"summary": "", "sections": []}))
    monkeypatch.setattr(resume_llm, "call_llm", call)
    tailor(fitness_score=None)
    prompt = call.call_args.kwargs["prompt"]
    assert "No fitness assessment available yet." in prompt


def test_tailor_prompt_includes_fitness_assessment_details(monkeypatch):
    call = Mock(return_value=json.dumps({"summary": "", "sections": []}))
    monkeypatch.setattr(resume_llm, "call_llm", call)
    fitness_score = {
        "overall_score": 62,
        "summary": "Solid backend fit but missing NextJS.",
        "matched_keywords": ["Python"],
        "missing_keywords": ["NextJS", "GraphQL"],
        "overqualification_note": "15+ years vs. a role scoped for 2-4 years.",
        "category_scores": [
            {
                "category": "required_skills", "score": 28, "max_score": 50,
                "weaknesses": ["NextJS not demonstrated"],
            },
            {"category": "responsibilities", "score": 30, "max_score": 30, "weaknesses": []},
        ],
    }
    tailor(fitness_score=fitness_score)
    prompt = call.call_args.kwargs["prompt"]
    assert "Overall fit score: 62/100" in prompt
    assert "Solid backend fit but missing NextJS." in prompt
    assert "required_skills: 28/50 — weaknesses: NextJS not demonstrated" in prompt
    assert "responsibilities: 30/30" in prompt
    assert "Missing keywords/qualifications: NextJS, GraphQL" in prompt
    assert "Overqualification risk note: 15+ years vs. a role scoped for 2-4 years." in prompt


def test_tailor_prompt_formats_resume_and_job(monkeypatch):
    call = Mock(return_value=json.dumps({"summary": "", "sections": []}))
    monkeypatch.setattr(resume_llm, "call_llm", call)
    tailor(resume="Built APIs.", job="Requires APIs.")
    prompt = call.call_args.kwargs["prompt"]
    assert "Built APIs." in prompt
    assert "Requires APIs." in prompt
