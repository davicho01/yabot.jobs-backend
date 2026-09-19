import json
import random
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from app.models.enums import AtsType, EmploymentType
from app.services.adapters import base
from app.services.adapters.base import (
    DEFAULT_MAX_JOBS_PER_CRAWL,
    EMPLOYMENT_TYPE_MAP,
    TIMEOUT,
    AtsAdapter,
    ExtractedJobFields,
    ScanResult,
    get_with_retry,
)
from app.services.adapters.text import MAX_LOCATION_LENGTH, clean_text, html_to_formatted_text

_WALMART_SITEMAP_URL = "https://careers.walmart.com/sitemap.xml"
_WALMART_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_WALMART_URL_RE = re.compile(r"careers\.walmart\.com", re.IGNORECASE)
# Unquoted attribute, verified live: <script id=__NEXT_DATA__ type="application/json">
_NEXT_DATA_RE = re.compile(r'<script id=["\']?__NEXT_DATA__["\']?[^>]*>(.*?)</script>', re.DOTALL)


def _match(url: str) -> str | None:
    return "walmart" if _WALMART_URL_RE.search(url) else None


def _fetch_jobs(board_key: str) -> list[str]:  # noqa: ARG001 - single-company board, fixed key, no key needed
    # robots.txt disallows /api and /results — the live search there is an
    # AI-chat-backed GraphQL endpoint (session/thread-based, no plain
    # listing call) anyway, so sitemap.xml is the only robots-allowed
    # discovery source. It lists every live posting under /us/en/jobs/{id}
    # but with no lastmod/date per URL, and at ~16k entries vastly exceeds
    # our per-crawl cap. Taking a fixed slice would return the exact same
    # _WALMART_MAX_JOBS URLs forever (get_or_create_job_posting no-ops on
    # already-seen URLs, so nothing new would ever surface) — sampling a
    # different date-seeded slice each day instead cycles the whole board
    # through over ~32 days, giving newly-added postings a chance to appear
    # without needing a freshness signal the sitemap doesn't provide.
    response = get_with_retry(_WALMART_SITEMAP_URL, timeout=TIMEOUT)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [
        loc.text
        for loc in root.findall(".//sm:loc", ns)
        if loc.text and urlsplit(loc.text).path.startswith("/us/en/jobs/")
    ]
    if len(urls) <= _WALMART_MAX_JOBS:
        return urls
    today = datetime.now(timezone.utc).date()
    return random.Random(today.toordinal()).sample(urls, _WALMART_MAX_JOBS)


# The job detail page is a client-rendered Next.js app, but the initial HTML
# still embeds the full job payload (title, description, location, pay, ...)
# server-side as JSON in a `<script id=__NEXT_DATA__>` tag — no separate API
# call needed (verified live: R-1075582 and a random sample of other
# requisition IDs across both the Walmart and Sam's Club brands).
def _extract_job_data(html: str) -> dict[str, Any] | None:
    match = _NEXT_DATA_RE.search(html)
    if match is None:
        return None
    try:
        data = json.loads(match.group(1))
        job_details = data["props"]["pageProps"]["jobDetails"]
        return job_details if isinstance(job_details, dict) else None
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _location_of(job_data: dict[str, Any]) -> str | None:
    location = job_data.get("primaryLocation")
    if not isinstance(location, dict):
        return None
    bits = [location.get("city"), location.get("stateCode"), location.get("country")]
    location_str = ", ".join(b for b in bits if isinstance(b, str) and b.strip()) or None
    if location_str and len(location_str) > MAX_LOCATION_LENGTH:
        location_str = location_str[: MAX_LOCATION_LENGTH - 3] + "..."
    return location_str


def _posted_at_of(job_data: dict[str, Any]) -> date | None:
    posted = job_data.get("jobPostingStartDate")
    if not isinstance(posted, str):
        return None
    try:
        return date.fromisoformat(posted[:10])
    except ValueError:
        return None


def _employment_type_of(job_data: dict[str, Any]) -> str:
    employment_types = job_data.get("employmentTypes")
    if not isinstance(employment_types, list) or not employment_types:
        return EmploymentType.UNKNOWN
    first = employment_types[0]
    value = first.get("value") if isinstance(first, dict) else None
    if not isinstance(value, str):
        return EmploymentType.UNKNOWN
    normalized = value.strip().upper().replace("-", "_").replace(" ", "_")
    return EMPLOYMENT_TYPE_MAP.get(normalized, EmploymentType.UNKNOWN)


# payRange is a list of per-location pay bands (multi-location postings get
# one entry per location) rather than a single figure — same "widest band
# wins" reasoning as base.salary_from_text's multi-level handling. Units
# vary per posting (hourly for retail roles, annual for corporate ones, per
# payFrequency) but ScanResult has no period field, same tradeoff every
# other adapter's raw salary numbers already make.
def _salary_of(job_data: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
    pay_range = job_data.get("payRange")
    if not isinstance(pay_range, list) or not pay_range:
        return None, None, None
    salary_min: int | None = None
    salary_max: int | None = None
    for band in pay_range:
        if not isinstance(band, dict):
            continue
        try:
            band_min = round(float(band["min"]))
            band_max = round(float(band["max"]))
        except (KeyError, TypeError, ValueError):
            continue
        salary_min = band_min if salary_min is None else min(salary_min, band_min)
        salary_max = band_max if salary_max is None else max(salary_max, band_max)
    if salary_min is None:
        return None, None, None
    return salary_min, salary_max, "USD"


def _title_of(job_data: dict[str, Any]) -> str | None:
    title = job_data.get("jobPostingTitle") or job_data.get("title")
    return clean_text(title) if isinstance(title, str) else None


# jobPostingDescription is the full posting shown on the page (shift
# schedules, "Position Summary", "What you'll do", qualifications); the
# sibling `description` field is a shorter, incomplete duplicate of just
# part of it (verified live: description omits the schedule/summary
# sections jobPostingDescription includes).
def _description_of(job_data: dict[str, Any]) -> str | None:
    html = job_data.get("jobPostingDescription")
    return html_to_formatted_text(html) if isinstance(html, str) and html.strip() else None


# Walmart and Sam's Club share this one career site (careers.walmart.com)
# but are separate brands — jobDetails.brand ("Walmart" or "Sam's Club",
# already correctly capitalized) is the real per-job company name, distinct
# from the board-level CrawlSource.name.
def _company_name_of(job_data: dict[str, Any]) -> str | None:
    brand = job_data.get("brand")
    return clean_text(brand) if isinstance(brand, str) and brand.strip() else None


def extract(job_data: dict[str, Any]) -> ExtractedJobFields:
    return ExtractedJobFields(
        title=_title_of(job_data),
        description=_description_of(job_data),
        company_name=_company_name_of(job_data),
        location=_location_of(job_data),
        posted_at=_posted_at_of(job_data),
        employment_type=_employment_type_of(job_data),
    )


def scan_job_url(url: str) -> ScanResult | None:
    if not _WALMART_URL_RE.search(url):
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    html = page.text
    job_data = _extract_job_data(html)
    fields = extract(job_data) if job_data else None
    description = (fields.description if fields else None) or base.fallback_description(html)
    salary_min, salary_max, salary_currency = (
        _salary_of(job_data) if job_data else (None, None, None)
    )
    return ScanResult(
        success=True,
        title=(fields.title if fields else None) or base.fallback_title(html),
        description=description,
        company_name=(fields.company_name if fields else None) or "Walmart",
        location=fields.location if fields else None,
        employment_type=(fields.employment_type if fields else EmploymentType.UNKNOWN),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=fields.posted_at if fields else None,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.WALMART,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://careers.walmart.com",
    scan_job_url=scan_job_url,
)
