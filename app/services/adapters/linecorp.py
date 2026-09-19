import re
from datetime import date
from typing import Any

import httpx

from app.models.enums import AtsType, EmploymentType
from app.services.adapters.base import (
    DEFAULT_MAX_JOBS_PER_CRAWL,
    EMPLOYMENT_TYPE_MAP,
    TIMEOUT,
    AtsAdapter,
    ScanResult,
    get_with_retry,
)
from app.services.adapters.text import clean_text, html_to_formatted_text

# LINE's career site (careers.linecorp.com) is a Gatsby static site backed
# by Strapi — verified live: every page ships a matching
# /page-data/{path}/page-data.json carrying the exact GraphQL data used to
# render it, including (on /jobs/) the *entire* job catalog inline as
# allStrapiJobs (no separate listing API), and (on /jobs/{id}/) that one
# job's full record as strapiJobs. No JS execution needed for either.
_LINE_JOBS_PAGE_DATA_URL = "https://careers.linecorp.com/page-data/jobs/page-data.json"
_LINE_JOB_PAGE_DATA_URL = "https://careers.linecorp.com/page-data/jobs/{job_id}/page-data.json"
_LINE_JOB_URL = "https://careers.linecorp.com/jobs/{job_id}/"
# Broad host check — matches both a submitted job URL and the canonical
# board_url (which has no job id in it at all, just .../jobs/).
_LINE_URL_RE = re.compile(r"careers\.linecorp\.com", re.IGNORECASE)
# Job id extraction, only meaningful against an actual job URL.
_LINE_JOB_ID_RE = re.compile(r"careers\.linecorp\.com/jobs/(\d+)", re.IGNORECASE)
_LINE_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL


def _match(url: str) -> str | None:
    return "linecorp" if _LINE_URL_RE.search(url) else None


def _fetch_jobs(board_key: str) -> list[str]:  # noqa: ARG001 - single-company board, fixed key, no key needed
    response = get_with_retry(_LINE_JOBS_PAGE_DATA_URL, timeout=TIMEOUT)
    response.raise_for_status()
    edges = response.json()["result"]["data"]["allStrapiJobs"]["edges"]
    # The static snapshot includes closed/draft postings too (verified
    # live: 306 of 382 entries have publish=false) — only publish=true is
    # actually open.
    urls = [
        _LINE_JOB_URL.format(job_id=node["strapiId"])
        for edge in edges
        if (node := edge["node"]).get("publish") and node.get("strapiId")
    ]
    return urls[:_LINE_MAX_JOBS]


def _posted_at_of(job: dict[str, Any]) -> date | None:
    start_date = job.get("start_date")
    if not isinstance(start_date, str):
        return None
    try:
        return date.fromisoformat(start_date[:10])
    except ValueError:
        return None


def _location_of(job: dict[str, Any]) -> str | None:
    cities = job.get("cities") or []
    countries = job.get("countries") or []
    city = cities[0].get("name") if cities and isinstance(cities[0], dict) else None
    country = countries[0].get("name") if countries and isinstance(countries[0], dict) else None
    parts = [p for p in (city, country) if isinstance(p, str) and p.strip()]
    return ", ".join(parts) or None


def _employment_type_of(job: dict[str, Any]) -> str:
    employment_types = job.get("employment_type") or []
    if not employment_types or not isinstance(employment_types[0], dict):
        return EmploymentType.UNKNOWN
    value = employment_types[0].get("name")
    if not isinstance(value, str):
        return EmploymentType.UNKNOWN
    normalized = value.strip().upper().replace("-", "_").replace(" ", "_")
    return EMPLOYMENT_TYPE_MAP.get(normalized, EmploymentType.UNKNOWN)


def _company_name_of(job: dict[str, Any]) -> str | None:
    companies = job.get("companies") or []
    if not companies or not isinstance(companies[0], dict):
        return None
    return clean_text(companies[0].get("name"))


def scan_job_url(url: str) -> ScanResult | None:
    match = _LINE_JOB_ID_RE.search(url)
    if match is None:
        return None
    job_id = match.group(1)
    try:
        response = get_with_retry(_LINE_JOB_PAGE_DATA_URL.format(job_id=job_id), timeout=TIMEOUT)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    job = response.json()["result"]["data"].get("strapiJobs")
    if not isinstance(job, dict):
        return ScanResult(success=False, error=f"No strapiJobs data for job {job_id}")

    # title_en is blank on some postings (internal/local-market roles never
    # given an English translation, verified live) — falls back to the
    # native title rather than leaving it empty.
    title = clean_text(job.get("title_en")) or clean_text(job.get("title"))
    content = job.get("content")

    return ScanResult(
        success=True,
        title=title,
        description=html_to_formatted_text(content) if isinstance(content, str) else None,
        company_name=_company_name_of(job) or "LINE",
        location=_location_of(job),
        employment_type=_employment_type_of(job),
        posted_at=_posted_at_of(job),
    )


ADAPTER = AtsAdapter(
    AtsType.LINECORP,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://careers.linecorp.com/jobs/",
    scan_job_url=scan_job_url,
)
