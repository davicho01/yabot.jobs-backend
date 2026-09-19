import re
from datetime import date
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import (
    DEFAULT_MAX_JOBS_PER_CRAWL,
    TIMEOUT,
    AtsAdapter,
    ExtractedJobFields,
    ScanResult,
    get_with_retry,
)
from app.services.adapters.text import html_to_formatted_text

_NLX_API = "https://prod-search-api.jobsyn.org/api/v1/solr/search"
# Present in every NLX-templated career page's server-rendered HTML (favicon/
# og:image point at seo.nlx.org) even on a plain fetch — verified against both
# a listing page and a job detail page live, no JS execution needed.
_NLX_SIGNATURE = "nlx.org"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    return _SLUG_RE.sub("-", value.lower()).strip("-")


def _job_url(host: str, job: dict) -> str | None:
    # No "url" field in the API response at all — verified live. The site's
    # own job-detail links are {location-slug}/{title_slug}/{guid}/job/,
    # where location-slug is just location_exact slugified (matches both a
    # "City, ST" shape and placeholders like "Virtual, USA" — verified
    # against live listing-page hrefs for several jobs of each shape).
    guid = job.get("guid")
    title_slug = job.get("title_slug")
    location = job.get("location_exact")
    if not (guid and title_slug and location):
        return None
    return f"https://{host}/{_slugify(location)}/{title_slug}/{guid}/job/"


def _fetch_jobs(host: str) -> list[str]:
    # NLX (National Labor Exchange, seo.nlx.org/jobsyn.org) is a career-site
    # SEO layer white-labeled onto the company's own domain — every tenant
    # looks like any other company's careers page without fetching it first,
    # same reasoning as clinch.py/eightfold.py. Its job search API is one
    # shared multi-tenant host with no board-key query param at all: the
    # tenant is selected purely by the X-Origin header matching the
    # requesting company's own hostname (verified live). Page size is fixed
    # server-side at 15 regardless of any num_items requested (verified
    # live), so pagination just walks `page` until the response says there's
    # no more.
    urls: list[str] = []
    page = 1
    while len(urls) < DEFAULT_MAX_JOBS_PER_CRAWL:
        response = get_with_retry(
            _NLX_API,
            params={"page": page},
            headers={"X-Origin": host, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        urls.extend(url for job in data.get("jobs", []) if (url := _job_url(host, job)))
        if not data.get("pagination", {}).get("has_more_pages"):
            break
        page += 1
    return urls[:DEFAULT_MAX_JOBS_PER_CRAWL]


def _detect_embedded(url: str) -> str | None:
    host = urlsplit(url).netloc
    if not host:
        return None
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
    except httpx.HTTPError:
        return None
    # No raise_for_status(): some tenants (verified live: sandia.jobs) serve
    # a real, fully-populated NLX-templated page with an HTTP 404 status on
    # every route — a client-side-routed SPA's catch-all misreporting status,
    # not an actual missing page — so a status check here would wrongly
    # treat a working board as undetectable.
    return host if _NLX_SIGNATURE in response.text else None


def _board_key(url: str) -> str | None:
    return urlsplit(url).netloc or None


# NLX job pages are a Nuxt SPA with no JSON-LD and no useful og: tags —
# every route shares the same static "Dispatch Careers Home Page"-style
# meta description regardless of which job it is. But the page itself
# fetches its content client-side from a static, unauthenticated per-job
# JSON file keyed by the exact guid already sitting in the URL — verified
# live via the browser network tab — that carries the full structured
# record (title, company, location, schedule, and complete HTML
# description) no plain-fetch/og: scraping could ever recover.
_JOB_URL_RE = re.compile(r"https?://([^/]+)/[^/]+/[^/]+/([0-9A-Fa-f]{32})/job/?", re.IGNORECASE)
_DETAIL_URL = "https://microsites.dejobs.org/{job_folder}/data/{guid}.json"
# job_type has also been observed as "Per Diem" (healthcare as-needed work,
# verified live) which doesn't cleanly map to any EmploymentType value —
# left unmapped -> UNKNOWN like every other adapter's unrecognized case.
_JOB_TYPE_MAP = {
    "full time": EmploymentType.FULL_TIME,
    "part time": EmploymentType.PART_TIME,
}
_JOB_SHIFT_MAP = {
    "remote": WorkplaceType.REMOTE,
    "on-site": WorkplaceType.ONSITE,
    "hybrid": WorkplaceType.HYBRID,
}


def _fetch_job_data(url: str) -> dict[str, Any] | None:
    match = _JOB_URL_RE.search(url)
    if match is None:
        return None
    host, guid = match.groups()
    try:
        response = httpx.get(_DETAIL_URL.format(job_folder=host.replace(".", "-"), guid=guid), timeout=10.0)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _employment_type_of(job_data: dict[str, Any]) -> str:
    job_type = job_data.get("job_type")
    if isinstance(job_type, str):
        return _JOB_TYPE_MAP.get(job_type.strip().lower(), EmploymentType.UNKNOWN)
    return EmploymentType.UNKNOWN


def _workplace_type_of(job_data: dict[str, Any]) -> str:
    job_shift = job_data.get("job_shift")
    if isinstance(job_shift, str):
        return _JOB_SHIFT_MAP.get(job_shift.strip().lower(), WorkplaceType.UNKNOWN)
    return WorkplaceType.UNKNOWN


def _posted_at_of(job_data: dict[str, Any]) -> date | None:
    added = job_data.get("date_added")
    if not isinstance(added, str):
        return None
    try:
        return date.fromisoformat(added[:10])
    except ValueError:
        return None


def _description_of(job_data: dict[str, Any]) -> str | None:
    html = job_data.get("html_description")
    return html_to_formatted_text(html) if isinstance(html, str) and html.strip() else None


def extract(url: str, _html: str) -> ExtractedJobFields | None:
    job_data = _fetch_job_data(url)
    if job_data is None:
        return None
    title = job_data.get("title")
    company = job_data.get("company")
    location = job_data.get("location")
    return ExtractedJobFields(
        title=title if isinstance(title, str) else None,
        description=_description_of(job_data),
        company_name=company if isinstance(company, str) else None,
        location=location if isinstance(location, str) else None,
        workplace_type=_workplace_type_of(job_data),
        employment_type=_employment_type_of(job_data),
        posted_at=_posted_at_of(job_data),
    )


def scan_job_url(url: str) -> ScanResult | None:
    if not _JOB_URL_RE.search(url):
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    html = page.text
    fields = extract(url, html)
    if fields is None:
        # No useful generic fallback here — every NLX route shares the same
        # static meta description/title regardless of which job it is (see
        # this module's own docstring above), so falling back to it would
        # be actively wrong rather than merely incomplete.
        return ScanResult(success=False, error="Couldn't fetch this NLX posting's job data.")

    salary_min, salary_max, salary_currency = base.salary_from_text(fields.description)
    return ScanResult(
        success=True,
        title=fields.title,
        description=fields.description,
        company_name=fields.company_name,
        location=fields.location,
        workplace_type=fields.workplace_type,
        employment_type=fields.employment_type,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=fields.posted_at,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


# NLX has no static URL shape (match=None) — the company's own domain *is*
# the board, only distinguishable via _detect_embedded's signature check,
# same as Clinch/Eightfold. board_key for listing is just the host, trivially
# recoverable from any stored board_url. No to_board_url — the board lives on
# the company's own domain, not a shared NLX-hosted host, so board_url is
# stored verbatim (same reasoning as Oracle Fusion/Clinch).
ADAPTER = AtsAdapter(
    AtsType.NLX,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
    embedded_match=_detect_embedded,
    scan_job_url=scan_job_url,
)
