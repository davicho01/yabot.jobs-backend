import json
import re
from datetime import date
from typing import Any

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import (
    DEFAULT_MAX_JOBS_PER_CRAWL,
    RECENT_WINDOW_DAYS,
    TIMEOUT,
    AtsAdapter,
    ExtractedJobFields,
    ScanResult,
    get_with_retry,
    is_recent_posting,
    parse_month_day_year,
)
from app.services.adapters.text import MAX_LOCATION_LENGTH, clean_text, html_to_formatted_text

_APPLE_JOBS_URL = "https://jobs.apple.com/en-us/search"
_APPLE_JOB_URL = "https://jobs.apple.com/en-us/details/{position_id}/{slug}"
_APPLE_HYDRATION_RE = re.compile(r'window\.__staticRouterHydrationData = JSON\.parse\("(.*?)"\);', re.DOTALL)
_APPLE_PAGE_SIZE = 20
_APPLE_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_APPLE_URL_RE = re.compile(r"jobs\.apple\.com", re.IGNORECASE)


def _match(url: str) -> str | None:
    return "apple" if _APPLE_URL_RE.search(url) else None


def _fetch_jobs(board_key: str) -> list[str]:  # noqa: ARG001 - single-company board, fixed key, no key needed
    # No public API — the search page server-renders full job data into a
    # `window.__staticRouterHydrationData = JSON.parse("...")` blob (a React
    # Router hydration payload): a double-escaped JSON string, more fragile
    # than a real endpoint (same caveat as JazzHR) but the data itself is
    # clean structured JSON, not raw HTML to regex-scrape. sort=newest
    # verified newest-first (page 1 was entirely today's postingDate, page
    # 10 was already yesterday's), so the same RECENT_WINDOW_DAYS early-exit
    # as Workday/Amazon applies — using postingDate (a stable per-job date),
    # NOT postDateInGMT, which turned out to change on every request for
    # the same job (a live response timestamp, not a stored value).
    urls: list[str] = []
    page = 1
    while len(urls) < _APPLE_MAX_JOBS:
        response = get_with_retry(_APPLE_JOBS_URL, params={"sort": "newest", "page": page}, timeout=TIMEOUT)
        response.raise_for_status()
        match = _APPLE_HYDRATION_RE.search(response.text)
        if not match:
            break
        # The blob is a JS string literal fed to JSON.parse — re-wrapping it
        # in quotes and parsing through json.loads (rather than the
        # `unicode_escape` codec) unescapes it correctly without mangling
        # any real non-ASCII characters already in the text.
        search_data = json.loads(json.loads('"' + match.group(1) + '"'))["loaderData"]["search"]
        postings = search_data.get("searchResults", [])
        if not postings:
            break

        recent_postings = [
            posting
            for posting in postings
            if (posted := parse_month_day_year(posting.get("postingDate"), month_style="%b %d, %Y")) is not None
            and is_recent_posting(posted)
        ]
        urls.extend(
            _APPLE_JOB_URL.format(position_id=posting["positionId"], slug=posting["transformedPostingTitle"])
            for posting in recent_postings
            if posting.get("positionId") and posting.get("transformedPostingTitle")
        )
        if len(recent_postings) < len(postings) or len(postings) < _APPLE_PAGE_SIZE:
            break
        page += 1

    return urls[:_APPLE_MAX_JOBS]


# jobs.apple.com is a client-rendered SPA, but the initial HTML still
# embeds the full job payload (location, posting date, description, ...)
# as a JSON string passed to JSON.parse — the same
# `window.__staticRouterHydrationData` blob this adapter's own discovery
# side (above) reads from the *search* page, just under a different
# loaderData key ("jobDetails" instead of "search") on the job detail page.
def _extract_job_data(html: str) -> dict[str, Any] | None:
    match = _APPLE_HYDRATION_RE.search(html)
    if match is None:
        return None
    try:
        data = json.loads(json.loads(f'"{match.group(1)}"'))
        job_data = data["loaderData"]["jobDetails"]["jobsData"]
        return job_data if isinstance(job_data, dict) else None
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _location_of(job_data: dict[str, Any]) -> str | None:
    locations = job_data.get("localeLocation")
    if not isinstance(locations, list):
        return None
    parts = []
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        bits = [loc.get("city"), loc.get("stateProvince"), loc.get("countryName")]
        parts.append(", ".join(b for b in bits if isinstance(b, str) and b.strip()))
    location = "; ".join(p for p in dict.fromkeys(parts) if p) or None
    if location and len(location) > MAX_LOCATION_LENGTH:
        location = location[: MAX_LOCATION_LENGTH - 3] + "..."
    return location


def _posted_at_of(job_data: dict[str, Any]) -> date | None:
    posting_date = job_data.get("postingDateMeta")
    if not isinstance(posting_date, str):
        return None
    try:
        return date.fromisoformat(posting_date[:10])
    except ValueError:
        return None


def _title_of(job_data: dict[str, Any]) -> str | None:
    title = job_data.get("postingTitle")
    return clean_text(title) if isinstance(title, str) else None


# responsibilities/*Qualifications are plain text, one bullet item per
# line (verified on a live posting) rather than an HTML <ul>/<li> list like
# Oracle's equivalents, so each line needs an explicit "- " marker before
# going through html_to_formatted_text — otherwise it survives as an
# unbulleted paragraph and the list structure is lost.
def _bulleted(text: str) -> str:
    return "\n".join(f"- {line.strip()}" for line in text.split("\n") if line.strip())


# postingFooters carries the Pay & Benefits / EEO Statement / Accessibility
# / Application Deadline sections that render below Preferred Qualifications
# on the real posting, keyed by postLocationId (one entry per job location;
# taking the first is fine since they're the same boilerplate/comp text
# per-region rather than per-job). Each has its own displayOrder, so sort on
# that rather than trusting dict iteration order.
def _posting_footer_sections(job_data: dict[str, Any]) -> list[tuple[str, str]]:
    footers = job_data.get("postingFooters")
    if not isinstance(footers, list) or not footers:
        return []
    first = footers[0]
    if not isinstance(first, dict):
        return []
    sections = first.get("localizations", {}).get("en_US")
    if not isinstance(sections, list):
        return []
    ordered = sorted(
        (s for s in sections if isinstance(s, dict) and isinstance(s.get("content"), str) and s["content"].strip()),
        key=lambda s: s.get("displayOrder", 0),
    )
    return [(s.get("name") or "", s["content"]) for s in ordered]


def _description_of(job_data: dict[str, Any]) -> str | None:
    sections = [job_data.get("jobSummary"), job_data.get("description")]
    if job_data.get("responsibilities"):
        sections.append("<h3>Responsibilities</h3>" + _bulleted(job_data["responsibilities"]))
    if job_data.get("minimumQualifications"):
        sections.append("<h3>Minimum Qualifications</h3>" + _bulleted(job_data["minimumQualifications"]))
    if job_data.get("preferredQualifications"):
        sections.append("<h3>Preferred Qualifications</h3>" + _bulleted(job_data["preferredQualifications"]))
    for name, content in _posting_footer_sections(job_data):
        sections.append(f"<h3>{name}</h3>" + content if name else content)
    html = "\n\n".join(s for s in sections if isinstance(s, str) and s.strip())
    return html_to_formatted_text(html) if html else None


# Amazon, Google, and Apple's own in-house career sites (unlike a
# multi-tenant ATS such as Greenhouse) each host exactly one company's
# postings, ship no JSON-LD hiringOrganization, and have no on-page field
# naming the company at all — so the company name isn't "extracted", it's
# just known from which site this is.
def extract(url: str, html: str) -> ExtractedJobFields | None:
    # company_name is gated on the URL (same as _match above), independent
    # of whether the hydration blob below parses — matching how
    # job_scanner.py's generic _single_company_name_for_url table (URL-
    # only) was never conditioned on the blob's own success.
    is_apple = bool(_APPLE_URL_RE.search(url))
    job_data = _extract_job_data(html)
    if job_data is None:
        return ExtractedJobFields(company_name="Apple") if is_apple else None
    return ExtractedJobFields(
        title=_title_of(job_data),
        description=_description_of(job_data),
        company_name="Apple" if is_apple else None,
        location=_location_of(job_data),
        posted_at=_posted_at_of(job_data),
    )


def scan_job_url(url: str) -> ScanResult | None:
    if not _APPLE_URL_RE.search(url):
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
    # Apple's own hydration blob never carries a workplace-type signal —
    # the og:description "Location | WorkplaceType" heuristic is the only
    # source for it, same as job_scanner.py's generic default scanner.
    og_description_raw = base.og_description_raw(html)
    _, workplace_type = (
        base.parse_og_description(og_description_raw) if og_description_raw else (None, WorkplaceType.UNKNOWN)
    )
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=(fields.title if fields else None) or base.og_title(html) or base.fallback_title(html),
        description=description,
        company_name="Apple",
        location=fields.location if fields else None,
        workplace_type=workplace_type,
        employment_type=EmploymentType.UNKNOWN,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=fields.posted_at if fields else None,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.APPLE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://jobs.apple.com",
    scan_job_url=scan_job_url,
)
