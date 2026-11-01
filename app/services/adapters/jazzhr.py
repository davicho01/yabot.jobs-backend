import re

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, limit_job_urls, AtsAdapter, ScanResult, get_with_retry
from app.services.adapters.text import clean_text, extract_balanced_div, html_to_formatted_text

_JAZZHR_JOBS_URL = "https://{board_key}.applytojob.com/apply/jobs"
# The listing page links to /apply/jobs/details/{id}, but that route serves
# a generic shell page (verified: <title>JazzHR » Job Listings</title>, no
# job-specific content) rather than the real posting. Each job's actual
# canonical page — confirmed via that shell page's own <link rel="canonical">
# — is /apply/{id}/{slug}, and the {slug} part turns out to be cosmetic:
# /apply/{id} alone (no slug) resolves to the same real content.
_JAZZHR_JOB_URL = "https://{board_key}.applytojob.com/apply/{job_id}"
_JAZZHR_JOB_ID_RE = re.compile(r"/apply/jobs/details/([a-zA-Z0-9]+)")
_JAZZHR_URL_RE = re.compile(r"([a-zA-Z0-9-]+)\.applytojob\.com", re.IGNORECASE)


def _match(url: str) -> str | None:
    match = _JAZZHR_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    # Unlike the other platforms, JazzHR has no public JSON API for listings
    # (their real API requires a per-company key, which we don't have and
    # can't get without that company's cooperation). The only free option is
    # the server-rendered /apply/jobs HTML page, which links to each posting
    # as /apply/jobs/details/{id} — more fragile than a real API contract
    # since it depends on markup that could change, but deterministic today.
    response = get_with_retry(_JAZZHR_JOBS_URL.format(board_key=board_key), timeout=TIMEOUT)
    response.raise_for_status()
    job_ids = dict.fromkeys(_JAZZHR_JOB_ID_RE.findall(response.text))  # dedupe, keep order
    if not job_ids:
        base.require_listing_page(response.text, "resumator", f"JazzHR board {board_key!r}")
    return limit_job_urls(_JAZZHR_JOB_URL.format(board_key=board_key, job_id=job_id) for job_id in job_ids)


# A job page's meta/og description is only "Apply to {title} at {company} in
# {location}." (all a generic scan ever got — verified 2026-10-12) and its
# JSON-LD is just the Organization. The posting itself is server-rendered:
# <div class='col ... description' id="job-description">, with the title in
# the job header's <h2> and location/type as titled attribute <div>s.
_JAZZHR_JOB_PAGE_RE = re.compile(r"[a-zA-Z0-9-]+\.applytojob\.com/apply/(?!jobs\b)[a-zA-Z0-9]+", re.IGNORECASE)
_DESCRIPTION_MARKER = 'id="job-description"'
_TITLE_RE = re.compile(r"class=[\"']job-header[\"'].*?<h2[^>]*>(.*?)</h2>", re.IGNORECASE | re.DOTALL)
_ATTRIBUTE_RE = r"<div[^>]*title=\"{name}\"[^>]*>\s*(?:<i[^>]*></i>)?(.*?)</div>"
_ORGANIZATION_RE = re.compile(r'"@type"\s*:\s*"Organization"\s*,\s*"name"\s*:\s*"((?:[^"\\]|\\.)*)"')
_EMPLOYMENT_TYPES = {
    "full time": EmploymentType.FULL_TIME,
    "part time": EmploymentType.PART_TIME,
    "contract": EmploymentType.CONTRACT,
    "contractor": EmploymentType.CONTRACT,
    "temporary": EmploymentType.TEMPORARY,
    "intern": EmploymentType.INTERNSHIP,
    "internship": EmploymentType.INTERNSHIP,
}


def _attribute(html: str, name: str) -> str | None:
    match = re.search(_ATTRIBUTE_RE.format(name=name), html, re.IGNORECASE | re.DOTALL)
    return clean_text(match.group(1)) if match else None


def _description_of(html: str) -> str | None:
    marker = html.find(_DESCRIPTION_MARKER)
    if marker == -1:
        return None
    div_start = html.rfind("<div", 0, marker)
    inner = extract_balanced_div(html, div_start) if div_start != -1 else None
    return html_to_formatted_text(inner) if inner else None


def scan_job_url(url: str) -> ScanResult | None:
    if not _JAZZHR_JOB_PAGE_RE.search(url):
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    html = page.text
    description = _description_of(html) or base.fallback_description(html)
    title_match = _TITLE_RE.search(html)
    company_match = _ORGANIZATION_RE.search(html)
    employment = (_attribute(html, "Type") or "").lower()
    location = _attribute(html, "Location")
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=(clean_text(title_match.group(1)) if title_match else None) or base.fallback_title(html),
        description=description,
        company_name=clean_text(company_match.group(1)) if company_match else None,
        location=location,
        workplace_type=WorkplaceType.REMOTE if (location or "").strip().lower() == "remote" else WorkplaceType.UNKNOWN,
        employment_type=_EMPLOYMENT_TYPES.get(employment, EmploymentType.UNKNOWN),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.JAZZHR,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://{key}.applytojob.com/apply/jobs",
    scan_job_url=scan_job_url,
)
