import json
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType, EmploymentType
from app.services.adapters import base
from app.services.adapters.base import (
    DEFAULT_MAX_JOBS_PER_CRAWL,
    TIMEOUT,
    AtsAdapter,
    ExtractedJobFields,
    ScanResult,
    get_with_retry,
    limit_job_urls,
)
from app.services.adapters.text import html_to_formatted_text

_RIPPLING_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_RIPPLING_URL_RE = re.compile(r"ats\.rippling\.com/([a-zA-Z0-9_-]+)", re.IGNORECASE)
_BOARD_URL = "https://ats.rippling.com/{slug}/jobs"
# Rippling's board is a Next.js app: the listing page server-renders the
# first page of results straight into __NEXT_DATA__ (react-query's
# dehydrated cache) rather than exposing a public REST endpoint — blind
# guesses at /api/... all 404 (verified live against PDQ.com's board). The
# page also honors a ?page= query param server-side (verified: requesting
# ?page=1 comes back with that page's data already baked into the same
# __NEXT_DATA__ shape), so pagination just means re-fetching the page with
# an incrementing page number, no separate API call needed at all.
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)
_PAGE_SIZE = 20
# A small, non-exhaustive map of the employmentType.label values seen live
# (only "SALARIED_FT" confirmed against a real posting) — anything else
# falls back to UNKNOWN rather than guessing.
_EMPLOYMENT_TYPE_MAP = {
    "SALARIED_FT": EmploymentType.FULL_TIME,
    "SALARIED_PT": EmploymentType.PART_TIME,
    "HOURLY_FT": EmploymentType.FULL_TIME,
    "HOURLY_PT": EmploymentType.PART_TIME,
    "CONTRACT": EmploymentType.CONTRACT,
    "INTERN": EmploymentType.INTERNSHIP,
    "TEMPORARY": EmploymentType.TEMPORARY,
}


def _match(url: str) -> str | None:
    match = _RIPPLING_URL_RE.search(url)
    return match.group(1) if match else None


def _board_url(slug: str) -> str:
    return _BOARD_URL.format(slug=slug)


def _board_key(url: str) -> str | None:
    return _match(url)


def _next_data(html: str) -> dict[str, Any] | None:
    match = _NEXT_DATA_RE.search(html)
    if match is None:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _job_posts_query(next_data: dict[str, Any]) -> dict[str, Any] | None:
    queries = next_data.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
    for query in queries:
        key = query.get("queryKey")
        if isinstance(key, list) and "job-posts" in key:
            return query
    return None


def _fetch_jobs(slug: str) -> list[str]:
    urls: list[str] = []
    page = 0
    while len(urls) < _RIPPLING_MAX_JOBS:
        response = get_with_retry(_board_url(slug), params={"page": page}, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        next_data = _next_data(response.text)
        query = _job_posts_query(next_data) if next_data else None
        data = query.get("state", {}).get("data") if query else None
        if not isinstance(data, dict):
            break
        items = data.get("items") or []
        urls.extend(item["url"] for item in items if item.get("url"))
        total_pages = data.get("totalPages") or 1
        if len(items) < _PAGE_SIZE or page >= total_pages - 1:
            break
        page += 1
    return limit_job_urls(urls)


def _posted_at(job_post: dict[str, Any]) -> date | None:
    raw = job_post.get("createdOn")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def extract(html: str) -> ExtractedJobFields | None:
    next_data = _next_data(html)
    if next_data is None:
        return None
    api_data = next_data.get("props", {}).get("pageProps", {}).get("apiData")
    if not isinstance(api_data, dict):
        return None
    job_post = api_data.get("jobPost")
    if not isinstance(job_post, dict):
        return None

    description_html = (job_post.get("description") or {}).get("company")
    locations = job_post.get("workLocations") or []
    employment_label = (job_post.get("employmentType") or {}).get("label")

    return ExtractedJobFields(
        title=job_post.get("name") if isinstance(job_post.get("name"), str) else None,
        description=html_to_formatted_text(description_html) if description_html else None,
        company_name=(api_data.get("jobBoard") or {}).get("companyName"),
        location="; ".join(loc for loc in locations if isinstance(loc, str)) or None,
        employment_type=_EMPLOYMENT_TYPE_MAP.get(employment_label, EmploymentType.UNKNOWN),
        posted_at=_posted_at(job_post),
    )


def scan_job_url(url: str) -> ScanResult | None:
    if _match(url) is None:
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    html = page.text
    fields = extract(html)
    if fields is None:
        return None
    description = fields.description or base.fallback_description(html) or base.og_description(html)
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=fields.title or base.fallback_title(html),
        description=description,
        company_name=fields.company_name,
        location=fields.location,
        employment_type=fields.employment_type,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=fields.posted_at,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


# No board root to point at other than the shared ats.rippling.com host —
# canonicalizes to {slug}/jobs same as any multi-tenant platform (Taleo,
# Workday).
ADAPTER = AtsAdapter(
    AtsType.RIPPLING,
    match=_match,
    board_key=_board_key,
    fetch_jobs=_fetch_jobs,
    to_board_url=_board_url,
    scan_job_url=scan_job_url,
)
