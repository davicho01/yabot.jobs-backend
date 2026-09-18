import re

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
    limit_job_urls,
)
from app.services.adapters.text import html_to_formatted_text

_GOOGLE_JOBS_URL = "https://www.google.com/about/careers/applications/jobs/results"
_GOOGLE_JOB_RE = re.compile(r'href="jobs/results/([^"?]+)')
# Google has no per-job posting-date field anywhere (listing or detail
# page), unlike Workday/Amazon/Apple, so there's no way to reliably stop at
# a precise date cutoff. Pagination is bounded by the shared job cap.
_GOOGLE_PAGE_SIZE = 20
_GOOGLE_URL_RE = re.compile(r"google\.com/about/careers", re.IGNORECASE)


def _match(url: str) -> str | None:
    return "google" if _GOOGLE_URL_RE.search(url) else None


def _fetch_jobs(board_key: str) -> list[str]:  # noqa: ARG001 - single-company board, fixed key, no key needed
    # No public API — the search-results page server-renders real job links
    # directly into static HTML (no JS execution needed), verified against
    # 3362 live postings, 20 per page. sort_by=date changes result order
    # (verified against the unsorted default) but there's no per-job date
    # anywhere to confirm it's a true chronological cutoff.
    urls: list[str] = []
    for page in range(1, (DEFAULT_MAX_JOBS_PER_CRAWL + _GOOGLE_PAGE_SIZE - 1) // _GOOGLE_PAGE_SIZE + 1):
        response = get_with_retry(_GOOGLE_JOBS_URL, params={"page": page, "sort_by": "date"}, timeout=TIMEOUT)
        response.raise_for_status()
        job_paths = dict.fromkeys(_GOOGLE_JOB_RE.findall(response.text))  # dedupe, keep order
        if not job_paths:
            break
        urls.extend(f"{_GOOGLE_JOBS_URL}/{path}" for path in job_paths)
        if len(job_paths) < _GOOGLE_PAGE_SIZE:
            break
    return limit_job_urls(urls)


# Google's careers pages ship no JSON-LD JobPosting data at all, and their
# <meta name="description"> only carries the "About the job" blurb — the
# Minimum/Preferred qualifications and Responsibilities sections live
# further down the real, server-rendered page body with no structured data
# of their own, just an <h3> heading per section. Anchored on that heading
# text (stable) through the "bE3reb" class (the wrapper for the page's
# legal/EEO boilerplate that immediately follows this content on every
# posting observed) rather than Google's other minified CSS class names,
# which could change without notice. Falls back to no-op (caller keeps the
# meta-description-only text) if this shape isn't found on a given page.
_QUALIFICATIONS_START_RE = re.compile(r"<h3>\s*Minimum\s+Qualifications", re.IGNORECASE)
_SECTION_END_MARKER = 'class="bE3reb"'


def _description_of(html: str) -> str | None:
    start_match = _QUALIFICATIONS_START_RE.search(html)
    if start_match is None:
        return None
    marker = html.find(_SECTION_END_MARKER, start_match.start())
    if marker == -1:
        return None
    # Cut at the start of that marker's own <div ...> tag, not the marker
    # text itself, so the tag's dangling open bracket doesn't leak into the
    # output as literal "<div" text (it has no closing ">" within the slice).
    end = html.rfind("<div", start_match.start(), marker)
    if end == -1:
        end = marker
    return html_to_formatted_text(html[start_match.start() : end])


def extract(url: str, html: str) -> ExtractedJobFields | None:
    # company_name is gated on the URL (same as _match above), independent
    # of whether the qualifications-body scrape below finds anything —
    # matching how job_scanner.py's generic _single_company_name_for_url
    # table (URL-only) was never conditioned on that scrape's own success.
    is_google = bool(_GOOGLE_URL_RE.search(url))
    description = _description_of(html)
    if not is_google and description is None:
        return None
    return ExtractedJobFields(
        description=description,
        company_name="Google" if is_google else None,
    )


def scan_job_url(url: str) -> ScanResult | None:
    if not _GOOGLE_URL_RE.search(url):
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    html = page.text
    fields = extract(url, html)
    description = (
        (fields.description if fields else None) or base.fallback_description(html) or base.og_description(html)
    )
    og_description_raw = base.og_description_raw(html)
    location, workplace_type = (
        base.parse_og_description(og_description_raw) if og_description_raw else (None, WorkplaceType.UNKNOWN)
    )
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=base.og_title(html) or base.fallback_title(html),
        description=description,
        company_name="Google",
        location=location,
        workplace_type=workplace_type,
        employment_type=EmploymentType.UNKNOWN,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.GOOGLE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://www.google.com/about/careers",
    scan_job_url=scan_job_url,
)
