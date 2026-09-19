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
from app.services.prompts import COVER_LETTER_PROMPT, REVIEW_PROMPT, SCORE_PROMPT, TAILOR_PROMPT

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


@dataclass
class ResumeScoreResult:
    overall_score: int = 0
    matched_keywords: list[str] = field(default_factory=list)
    missing_keywords: list[str] = field(default_factory=list)
    summary: str = ""
    raw_response: dict[str, Any] | None = None


def score_resume_with_llm(
    resume_text: str,
    job_description: str,
    *,
    provider: str,
    model: str | None,
    api_key: str,
    base_url: str | None,
) -> ResumeScoreResult:
    prompt = SCORE_PROMPT.format(
        resume_text=resume_text[:_MAX_TEXT_CHARS], job_description=job_description[:_MAX_TEXT_CHARS]
    )
    raw = call_llm(provider=provider, model=model, api_key=api_key, base_url=base_url, prompt=prompt)
    try:
        data = _parse_response(raw)
    except json.JSONDecodeError as exc:
        raise LlmError(f"Model response was not valid JSON: {exc}") from exc

    score = data.get("overall_score")
    try:
        score = max(0, min(100, int(score)))
    except (TypeError, ValueError):
        score = 0

    return ResumeScoreResult(
        overall_score=score,
        matched_keywords=_as_str_list(data.get("matched_keywords")),
        missing_keywords=_as_str_list(data.get("missing_keywords")),
        summary=data.get("summary") if isinstance(data.get("summary"), str) else "",
        raw_response=data,
    )


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


def generate_tailored_resume_with_llm(
    resume_text: str,
    job_description: str,
    *,
    provider: str,
    model: str | None,
    api_key: str,
    base_url: str | None,
) -> TailoredResumeContent:
    prompt = TAILOR_PROMPT.format(
        resume_text=resume_text[:_MAX_TEXT_CHARS], job_description=job_description[:_MAX_TEXT_CHARS]
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
