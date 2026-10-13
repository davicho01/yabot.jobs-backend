import re
from datetime import date
from typing import Any

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry, limit_job_urls
from app.services.adapters.text import clean_text

_BAMBOOHR_JOBS_URL = "https://{board_key}.bamboohr.com/careers/list"
_BAMBOOHR_JOB_URL = "https://{board_key}.bamboohr.com/careers/{job_id}"
_BAMBOOHR_DETAIL_URL = "https://{board_key}.bamboohr.com/careers/{job_id}/detail"
_BAMBOOHR_COMPANY_URL = "https://{board_key}.bamboohr.com/careers/company-info"
_BAMBOOHR_URL_RE = re.compile(r"([a-zA-Z0-9-]+)\.bamboohr\.com", re.IGNORECASE)
_JOB_ID_RE = re.compile(r"/careers/(\d+)")


def _match(url: str) -> str | None:
    match = _BAMBOOHR_URL_RE.search(url)
    return match.group(1) if match else None


def _search_postings(board_key: str) -> list[dict[str, Any]]:
    # Free, public, unauthenticated API — no key required.
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


# locationType on /careers/{id}/detail and /careers/list (verified live
# across tenants 2026-10-13): "1" on remote openings with no office
# location, "2" on ones titled "(Hybrid)", "0" on everything else.
_WORKPLACE_TYPES = {"0": WorkplaceType.ONSITE, "1": WorkplaceType.REMOTE, "2": WorkplaceType.HYBRID}


def _company_name(board_key: str) -> str | None:
    try:
        response = get_with_retry(_BAMBOOHR_COMPANY_URL.format(board_key=board_key), timeout=TIMEOUT)
        response.raise_for_status()
        return clean_text((response.json().get("result") or {}).get("name"))
    except (httpx.HTTPError, ValueError):
        return None


def _posted_at_of(job: dict[str, Any]) -> date | None:
    try:
        return date.fromisoformat(job.get("datePosted") or "")
    except ValueError:
        return None


def scan_job_url(url: str) -> ScanResult | None:
    # The job page is a client-rendered shell whose only description is the
    # meta/og teaser (~300 characters, cut mid-word), so read the posting
    # from the JSON the page itself loads instead.
    board_key = _match(url)
    job_id_match = _JOB_ID_RE.search(url)
    if board_key is None or job_id_match is None:
        return None

    detail_url = _BAMBOOHR_DETAIL_URL.format(board_key=board_key, job_id=job_id_match.group(1))
    try:
        response = get_with_retry(detail_url, timeout=TIMEOUT)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return ScanResult(success=False, error=str(exc), expired=exc.response.status_code == 404)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    job = (response.json().get("result") or {}).get("jobOpening") or {}
    status = job.get("jobOpeningStatus")
    if status and status != "Open":
        return ScanResult(success=False, error=f"BambooHR job is {status}.", expired=True)

    description = base.html_to_formatted_text(job.get("description"))
    salary_min, salary_max, salary_currency = base.salary_from_text(job.get("compensation"))
    if salary_min is None and salary_max is None:
        salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=clean_text(job.get("jobOpeningName")),
        description=description,
        company_name=_company_name(board_key),
        location=_location_of(job),
        workplace_type=_WORKPLACE_TYPES.get(job.get("locationType") or "", WorkplaceType.UNKNOWN),
        employment_type=_employment_type_of(job),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=_posted_at_of(job),
    )


ADAPTER = AtsAdapter(
    AtsType.BAMBOOHR,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://{key}.bamboohr.com/careers",
    scan_job_url=scan_job_url,
)
