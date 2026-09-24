import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.api_key import UserApiKey
from app.services.llm_client import LlmError, call_llm
from app.services.prompts import (
    COVER_LETTER_PROMPT,
    EVALUATION_PROMPT,
    INTERVIEW_PREP_PROMPT,
    QUICK_SCORE_PROMPT,
    REVIEW_PROMPT,
    TAILOR_PROMPT,
)

_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?|```$", re.MULTILINE)

# Generous relative to a typical resume/job-description once extracted to
# plain text, cheap at current per-token pricing — guards against
# truncating real content, not cost.
_MAX_TEXT_CHARS = 20_000


def get_users_default_llm_key(db: Session, user_id: uuid.UUID) -> UserApiKey:
    """Look up the user's own default LLM key for resume features (bring-
    your-own-key) — distinct from the system-wide key used for job
    crawling/scanning, which resume features must never fall back to.
    """
    key = db.scalar(
        select(UserApiKey).where(
            UserApiKey.user_id == user_id, UserApiKey.is_default.is_(True), UserApiKey.is_active.is_(True)
        )
    )
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Add a default LLM API key first via POST /api-keys before using resume features.",
        )
    return key


def _parse_response(raw: str) -> dict[str, Any]:
    text = _CODE_FENCE_RE.sub("", raw.strip()).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise LlmError("Expected a JSON object from the model.")
    return data


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


_CONTACT_KEYS = ("name", "email", "phone", "location", "linkedin")


def _as_contact_dict(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    contact = {key: value[key] for key in _CONTACT_KEYS if isinstance(value.get(key), str)}
    return contact or None


# --- Review ------------------------------------------------------------


@dataclass
class ResumeReviewResult:
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    summary: str = ""
    raw_response: dict[str, Any] | None = None


def review_resume_with_llm(
    resume_text: str, *, provider: str, model: str | None, api_key: str, base_url: str | None
) -> ResumeReviewResult:
    prompt = REVIEW_PROMPT.format(resume_text=resume_text[:_MAX_TEXT_CHARS])
    raw = call_llm(provider=provider, model=model, api_key=api_key, base_url=base_url, prompt=prompt)
    try:
        data = _parse_response(raw)
    except json.JSONDecodeError as exc:
        raise LlmError(f"Model response was not valid JSON: {exc}") from exc
    return ResumeReviewResult(
        strengths=_as_str_list(data.get("strengths")),
        weaknesses=_as_str_list(data.get("weaknesses")),
        suggestions=_as_str_list(data.get("suggestions")),
        summary=data.get("summary") if isinstance(data.get("summary"), str) else "",
        raw_response=data,
    )


# --- Score ---------------------------------------------------------------

# Points available per rubric category (see EVALUATION_PROMPT) —
# authoritative in code so a model that echoes back the wrong max_score
# can't corrupt it.
_CATEGORY_MAX = {
    "required_skills": 50,
    "responsibilities": 30,
    "seniority": 15,
    "preferred_qualifications": 5,
}


def _as_category_scores_list(value: Any, overall_score: int) -> list[dict[str, Any]]:
    """Build the 4-category breakdown, reconciled to sum to exactly
    `overall_score` — the model's own gut number, trusted as-is. The model is
    asked to make its per-category scores add up to it, but isn't reliably
    consistent, so any leftover/excess is deterministically shifted onto
    categories (in rubric order) that have room to absorb it. Each category
    is still capped at its own fixed point range (_CATEGORY_MAX), and since
    those caps sum to 100 and overall_score is always <= 100, reconciliation
    can always succeed.
    """
    items = value if isinstance(value, list) else []
    by_category = {item.get("category"): item for item in items if isinstance(item, dict)}

    scores: dict[str, int] = {}
    for category, max_score in _CATEGORY_MAX.items():
        item = by_category.get(category, {})
        try:
            scores[category] = max(0, min(max_score, int(item.get("score"))))
        except (TypeError, ValueError):
            scores[category] = 0

    delta = overall_score - sum(scores.values())
    for category, max_score in _CATEGORY_MAX.items():
        if delta == 0:
            break
        if delta > 0:
            add = min(max_score - scores[category], delta)
            scores[category] += add
            delta -= add
        else:
            take = min(scores[category], -delta)
            scores[category] -= take
            delta += take

    breakdown = []
    for category, max_score in _CATEGORY_MAX.items():
        item = by_category.get(category, {})
        breakdown.append(
            {
                "category": category,
                "score": scores[category],
                "max_score": max_score,
                "why": item.get("why") if isinstance(item.get("why"), str) else "",
                "job_requirements": _as_str_list(item.get("job_requirements")),
                "strengths": _as_str_list(item.get("strengths")),
                "weaknesses": _as_str_list(item.get("weaknesses")),
            }
        )
    return breakdown


@dataclass
class ResumeQuickScoreResult:
    overall_score: int = 0
    matched_keywords: list[str] = field(default_factory=list)
    missing_keywords: list[str] = field(default_factory=list)
    summary: str = ""
    # Informational only — does not affect overall_score, which stays
    # purely merit-based. See QUICK_SCORE_PROMPT.
    overqualification_note: str = ""
    raw_response: dict[str, Any] | None = None


def quick_score_resume_with_llm(
    resume_text: str,
    job_description: str,
    *,
    provider: str,
    model: str | None,
    api_key: str,
    base_url: str | None,
) -> ResumeQuickScoreResult:
    """Fast fit check: just the number, matched/missing keywords, and a
    short summary — no per-category breakdown. See evaluate_resume_with_llm
    for the slower, opt-in comprehensive follow-up.
    """
    prompt = QUICK_SCORE_PROMPT.format(
        resume_text=resume_text[:_MAX_TEXT_CHARS], job_description=job_description[:_MAX_TEXT_CHARS]
    )
    raw = call_llm(provider=provider, model=model, api_key=api_key, base_url=base_url, prompt=prompt)
    try:
        data = _parse_response(raw)
    except json.JSONDecodeError as exc:
        raise LlmError(f"Model response was not valid JSON: {exc}") from exc

    try:
        score = max(0, min(100, int(data.get("overall_score"))))
    except (TypeError, ValueError):
        score = 0

    return ResumeQuickScoreResult(
        overall_score=score,
        matched_keywords=_as_str_list(data.get("matched_keywords")),
        missing_keywords=_as_str_list(data.get("missing_keywords")),
        summary=data.get("summary") if isinstance(data.get("summary"), str) else "",
        overqualification_note=(
            data.get("overqualification_note") if isinstance(data.get("overqualification_note"), str) else ""
        ),
        raw_response=data,
    )


@dataclass
class ResumeEvaluationResult:
    category_scores: list[dict[str, Any]] = field(default_factory=list)
    raw_response: dict[str, Any] | None = None


def evaluate_resume_with_llm(
    resume_text: str,
    job_description: str,
    overall_score: int,
    *,
    provider: str,
    model: str | None,
    api_key: str,
    base_url: str | None,
) -> ResumeEvaluationResult:
    """Comprehensive, opt-in follow-up to quick_score_resume_with_llm: given
    an already-decided overall_score, produces the 4-category rubric
    breakdown explaining it. Slower (much larger output) than the quick
    score, which is why it's a separate, user-requested call.
    """
    prompt = EVALUATION_PROMPT.format(
        resume_text=resume_text[:_MAX_TEXT_CHARS],
        job_description=job_description[:_MAX_TEXT_CHARS],
        overall_score=overall_score,
    )
    raw = call_llm(provider=provider, model=model, api_key=api_key, base_url=base_url, prompt=prompt)
    try:
        data = _parse_response(raw)
    except json.JSONDecodeError as exc:
        raise LlmError(f"Model response was not valid JSON: {exc}") from exc

    # category_scores is reconciled to sum to exactly `overall_score` — see
    # _as_category_scores_list — rather than trusted verbatim from the model.
    category_scores = _as_category_scores_list(data.get("category_scores"), overall_score)

    return ResumeEvaluationResult(category_scores=category_scores, raw_response=data)


# --- Tailored generation ---------------------------------------------------


def _as_entries_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    entries = []
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("title"), str):
            entries.append(
                {
                    "title": item["title"],
                    "subtitle": item.get("subtitle") if isinstance(item.get("subtitle"), str) else None,
                    "bullets": _as_str_list(item.get("bullets")),
                }
            )
    return entries


def _as_sections_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    sections = []
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("heading"), str):
            sections.append(
                {
                    "heading": item["heading"],
                    "bullets": _as_str_list(item.get("bullets")),
                    "entries": _as_entries_list(item.get("entries")),
                }
            )
    return sections


@dataclass
class TailoredResumeContent:
    summary: str = ""
    sections: list[dict[str, Any]] = field(default_factory=list)
    contact: dict[str, str] | None = None
    raw_response: dict[str, Any] | None = None


def _format_fitness_assessment(fitness_score: dict[str, Any] | None) -> str:
    """Render a prior ResumeScore as plain text for TAILOR_PROMPT — just the
    parts relevant to closing gaps (scores, missing keywords, per-category
    weaknesses, overqualification risk), not the full raw record.
    """
    if not fitness_score:
        return "No fitness assessment available yet."

    lines = [f"Overall fit score: {fitness_score.get('overall_score', 0)}/100"]
    if fitness_score.get("summary"):
        lines.append(f"Summary: {fitness_score['summary']}")

    for category in fitness_score.get("category_scores") or []:
        line = f"- {category.get('category')}: {category.get('score', 0)}/{category.get('max_score', 0)}"
        weaknesses = category.get("weaknesses") or []
        if weaknesses:
            line += " — weaknesses: " + "; ".join(weaknesses)
        lines.append(line)

    missing = fitness_score.get("missing_keywords") or []
    if missing:
        lines.append("Missing keywords/qualifications: " + ", ".join(missing))

    if fitness_score.get("overqualification_note"):
        lines.append(f"Overqualification risk note: {fitness_score['overqualification_note']}")

    return "\n".join(lines)


def generate_tailored_resume_with_llm(
    resume_text: str,
    job_description: str,
    *,
    provider: str,
    model: str | None,
    api_key: str,
    base_url: str | None,
    fitness_score: dict[str, Any] | None = None,
) -> TailoredResumeContent:
    prompt = TAILOR_PROMPT.format(
        resume_text=resume_text[:_MAX_TEXT_CHARS],
        job_description=job_description[:_MAX_TEXT_CHARS],
        fitness_assessment=_format_fitness_assessment(fitness_score),
    )
    raw = call_llm(provider=provider, model=model, api_key=api_key, base_url=base_url, prompt=prompt)
    try:
        data = _parse_response(raw)
    except json.JSONDecodeError as exc:
        raise LlmError(f"Model response was not valid JSON: {exc}") from exc

    return TailoredResumeContent(
        summary=data.get("summary") if isinstance(data.get("summary"), str) else "",
        sections=_as_sections_list(data.get("sections")),
        contact=_as_contact_dict(data.get("contact")),
        raw_response=data,
    )


# --- Cover letter ----------------------------------------------------------


@dataclass
class CoverLetterContent:
    greeting: str = ""
    body_paragraphs: list[str] = field(default_factory=list)
    closing: str = ""
    contact: dict[str, str] | None = None
    raw_response: dict[str, Any] | None = None


def generate_cover_letter_with_llm(
    resume_text: str,
    job_description: str,
    *,
    provider: str,
    model: str | None,
    api_key: str,
    base_url: str | None,
) -> CoverLetterContent:
    prompt = COVER_LETTER_PROMPT.format(
        resume_text=resume_text[:_MAX_TEXT_CHARS], job_description=job_description[:_MAX_TEXT_CHARS]
    )
    raw = call_llm(provider=provider, model=model, api_key=api_key, base_url=base_url, prompt=prompt)
    try:
        data = _parse_response(raw)
    except json.JSONDecodeError as exc:
        raise LlmError(f"Model response was not valid JSON: {exc}") from exc

    return CoverLetterContent(
        greeting=data.get("greeting") if isinstance(data.get("greeting"), str) else "",
        body_paragraphs=_as_str_list(data.get("body_paragraphs")),
        closing=data.get("closing") if isinstance(data.get("closing"), str) else "",
        contact=_as_contact_dict(data.get("contact")),
        raw_response=data,
    )


# --- Interview prep ---------------------------------------------------------

_QUESTION_CATEGORIES = {"behavioral", "technical", "role_specific"}


def _as_questions_list(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    questions = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("question"), str):
            continue
        category = item.get("category")
        questions.append(
            {
                "question": item["question"],
                # An unrecognized/missing category still gets a question
                # people can use — falls back to the most general bucket
                # rather than dropping the question entirely.
                "category": category if category in _QUESTION_CATEGORIES else "role_specific",
                "approach": item.get("approach") if isinstance(item.get("approach"), str) else "",
            }
        )
    return questions


@dataclass
class InterviewPrepContent:
    likely_questions: list[dict[str, str]] = field(default_factory=list)
    talking_points: list[str] = field(default_factory=list)
    questions_to_ask: list[str] = field(default_factory=list)
    raw_response: dict[str, Any] | None = None


def generate_interview_prep_with_llm(
    resume_text: str,
    job_description: str,
    *,
    provider: str,
    model: str | None,
    api_key: str,
    base_url: str | None,
) -> InterviewPrepContent:
    prompt = INTERVIEW_PREP_PROMPT.format(
        resume_text=resume_text[:_MAX_TEXT_CHARS], job_description=job_description[:_MAX_TEXT_CHARS]
    )
    raw = call_llm(provider=provider, model=model, api_key=api_key, base_url=base_url, prompt=prompt)
    try:
        data = _parse_response(raw)
    except json.JSONDecodeError as exc:
        raise LlmError(f"Model response was not valid JSON: {exc}") from exc

    return InterviewPrepContent(
        likely_questions=_as_questions_list(data.get("likely_questions")),
        talking_points=_as_str_list(data.get("talking_points")),
        questions_to_ask=_as_str_list(data.get("questions_to_ask")),
        raw_response=data,
    )
