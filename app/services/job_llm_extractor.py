import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from html import unescape
from typing import Any

from app.models.enums import EmploymentType, WorkplaceType
from app.services.llm_client import LlmError, call_llm
from app.services.prompts import EXTRACTION_PROMPT

logger = logging.getLogger("app.job_llm_extractor")

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?|```$", re.MULTILINE)

_VALID_WORKPLACE_TYPES = {v.value for v in WorkplaceType}
_VALID_EMPLOYMENT_TYPES = {v.value for v in EmploymentType}

# Generous relative to typical job-posting length (even a long, verbose
# listing rarely exceeds this once tags/scripts/styles are stripped), and
# cheap at current per-token LLM pricing — the real risk this guards
# against is truncating the actual job content, not cost.
_MAX_PAGE_TEXT_CHARS = 50_000


@dataclass
class LlmExtraction:
    title: str | None = None
    company_name: str | None = None
    location: str | None = None
    workplace_type: str = WorkplaceType.UNKNOWN
    employment_type: str = EmploymentType.UNKNOWN
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    posted_at: date | None = None
    raw_response: dict[str, Any] | None = None


def html_to_text(html: str) -> str:
    """Strip script/style blocks and tags, leaving plain page text."""
    without_scripts = _SCRIPT_STYLE_RE.sub(" ", html)
    text = unescape(_TAG_RE.sub(" ", without_scripts))
    return re.sub(r"\s+", " ", text).strip()


def _parse_response(raw: str) -> dict[str, Any]:
    text = _CODE_FENCE_RE.sub("", raw.strip()).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise LlmError("Expected a JSON object from the model.")
    return data


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _to_extraction(data: dict[str, Any]) -> LlmExtraction:
    workplace_type = data.get("workplace_type")
    employment_type = data.get("employment_type")
    return LlmExtraction(
        title=data.get("title") if isinstance(data.get("title"), str) else None,
        company_name=data.get("company_name") if isinstance(data.get("company_name"), str) else None,
        location=data.get("location") if isinstance(data.get("location"), str) else None,
        workplace_type=workplace_type if workplace_type in _VALID_WORKPLACE_TYPES else WorkplaceType.UNKNOWN,
        employment_type=employment_type
        if employment_type in _VALID_EMPLOYMENT_TYPES
        else EmploymentType.UNKNOWN,
        salary_min=_as_int(data.get("salary_min")),
        salary_max=_as_int(data.get("salary_max")),
        salary_currency=data.get("salary_currency") if isinstance(data.get("salary_currency"), str) else None,
        posted_at=_as_date(data.get("posted_at")),
        raw_response=data,
    )


def extract_with_llm(
    page_text: str, *, provider: str, model: str | None, api_key: str, base_url: str | None
) -> LlmExtraction:
    """Ask the configured LLM to extract job fields from `page_text`.

    Raises LlmError on any failure (network, auth, malformed response) so
    the caller can fall back to heuristic extraction instead of failing the
    whole scan.
    """
    prompt = EXTRACTION_PROMPT.format(page_text=page_text[:_MAX_PAGE_TEXT_CHARS])
    raw = call_llm(provider=provider, model=model, api_key=api_key, base_url=base_url, prompt=prompt)
    try:
        data = _parse_response(raw)
    except json.JSONDecodeError as exc:
        raise LlmError(f"Model response was not valid JSON: {exc}") from exc
    return _to_extraction(data)
