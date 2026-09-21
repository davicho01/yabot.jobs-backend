import re
from typing import Any

import httpx

from app.models.enums import AtsType, EmploymentType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry, limit_job_urls

_BAMBOOHR_JOBS_URL = "https://{board_key}.bamboohr.com/careers/list"
_BAMBOOHR_JOB_URL = "https://{board_key}.bamboohr.com/careers/{job_id}"
_BAMBOOHR_URL_RE = re.compile(r"([a-zA-Z0-9-]+)\.bamboohr\.com", re.IGNORECASE)
_JOB_ID_RE = re.compile(r"/careers/(\d+)")


def _match(url: str) -> str | None:
    match = _BAMBOOHR_URL_RE.search(url)
    return match.group(1) if match else None


def _search_postings(board_key: str) -> list[dict[str, Any]]:
    # Free, public, unauthenticated API — no key required. Also carries
    # location/employment-status fields the job page itself never exposes
    # anywhere extractable (no JSON-LD, no embedded page state at all — a
    # pure client-rendered SPA, verified live) — used by scan_job_url below
    # too, not just for discovering URLs.
    response = get_with_retry(_BAMBOOHR_JOBS_URL.format(board_key=board_key), timeout=TIMEOUT)
    response.raise_for_status()
    return response.json().get("result", [])


def _fetch_jobs(board_key: str) -> list[str]:
    postings = _search_postings(board_key)
    return limit_job_urls(
        _BAMBOOHR_JOB_URL.format(board_key=board_key, job_id=posting["id"])
        for posting in postings
        if posting.get("id")
    )


def _location_of(posting: dict[str, Any]) -> str | None:
    location = posting.get("location") or {}
    parts = [location.get("city"), location.get("state")]
    text = ", ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
    return text or None


# employmentStatusLabel is free text, not a fixed enum on BambooHR's side
# (verified live across several tenants: "Full-Time", "Permanent Part-Time",
# "Temporary Full-Time", "Full/Part-Time", "Contractor", "Temporary
# (Project)", ...) — checked as substrings, temporary/contract first since
# they're the more specific qualifier on a combined label like "Temporary
# Full-Time". Genuinely ambiguous ones ("Full/Part-Time") fall through to
# UNKNOWN rather than guessing.
def _employment_type_of(posting: dict[str, Any]) -> str:
    label = (posting.get("employmentStatusLabel") or "").lower()
    if "temporary" in label:
        return EmploymentType.TEMPORARY
    if "contract" in label:
        return EmploymentType.CONTRACT
    if "part-time" in label and "full" not in label:
        return EmploymentType.PART_TIME
    if "full-time" in label and "part" not in label:
        return EmploymentType.FULL_TIME
    return EmploymentType.UNKNOWN


def scan_job_url(url: str) -> ScanResult | None:
    board_key = _match(url)
    job_id_match = _JOB_ID_RE.search(url)
    if board_key is None or job_id_match is None:
        return None
    job_id = job_id_match.group(1)

    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    html = page.text

    try:
        postings = _search_postings(board_key)
    except httpx.HTTPError:
        postings = []
    posting = next((p for p in postings if str(p.get("id")) == job_id), None)

    description = base.fallback_description(html) or base.og_description(html)
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=base.og_title(html) or base.fallback_title(html),
        description=description,
        company_name=base.og_site_name(html),
        location=_location_of(posting) if posting else None,
        employment_type=_employment_type_of(posting) if posting else EmploymentType.UNKNOWN,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.BAMBOOHR,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://{key}.bamboohr.com/careers",
    scan_job_url=scan_job_url,
)
