import re

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

_AMAZON_JOBS_URL = "https://www.amazon.jobs/en/search.json"
_AMAZON_JOB_BASE_URL = "https://www.amazon.jobs"
_AMAZON_PAGE_SIZE = 100
_AMAZON_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_AMAZON_URL_RE = re.compile(r"amazon\.jobs", re.IGNORECASE)


def _match(url: str) -> str | None:
    return "amazon" if _AMAZON_URL_RE.search(url) else None


def _fetch_jobs(board_key: str) -> list[str]:  # noqa: ARG001 - single-company board, fixed key, no key needed
    # Free, public, unauthenticated API — no key required. sort=recent
    # verified newest-first (offset=0 was entirely today's date, offset=200
    # was already yesterday's) — same RECENT_WINDOW_DAYS early-exit as
    # Workday.
    urls: list[str] = []
    offset = 0
    while len(urls) < _AMAZON_MAX_JOBS:
        response = get_with_retry(
            _AMAZON_JOBS_URL,
            params={"result_limit": _AMAZON_PAGE_SIZE, "offset": offset, "sort": "recent"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        jobs = response.json().get("jobs", [])
        if not jobs:
            break

        recent_jobs = [
            job
            for job in jobs
            if (posted := parse_month_day_year(job.get("posted_date"), month_style="%B %d, %Y")) is not None
            and is_recent_posting(posted)
        ]
        urls.extend(_AMAZON_JOB_BASE_URL + job["job_path"] for job in recent_jobs if job.get("job_path"))
        if len(recent_jobs) < len(jobs) or len(jobs) < _AMAZON_PAGE_SIZE:
            break
        offset += _AMAZON_PAGE_SIZE

    return urls[:_AMAZON_MAX_JOBS]


# amazon.jobs ships no JobPosting JSON-LD and no embedded JSON blob (unlike
# Apple's SPA) — it's a plain server-rendered page, so the full description
# lives directly in the HTML as a sequence of
# <div class="section"><h2>Heading</h2><p>...</p></div> blocks under
# #job-detail-body (Description / Basic Qualifications / Preferred
# Qualifications, in that order — verified on a live posting). The
# pay-range disclosure required by US pay-transparency law is embedded as
# free text at the tail of the last section rather than its own field, so
# stitching every section together (not just "Description") is what
# surfaces it to job_scanner.py's salary-from-prose fallback downstream.
# Location/team/job-category live separately in a <div class="sidebar"> of
# <div class="association {kind}-icon">...<ul class="association-content">
# blocks.
_SECTION_RE = re.compile(r'<div class="section"><h2[^>]*>(.*?)</h2>', re.IGNORECASE | re.DOTALL)
_ASSOCIATION_RE = re.compile(
    r'<div class="association ([a-z-]+)-icon[^"]*"[^>]*>.*?<ul class="association-content">(.*?)</ul>',
    re.IGNORECASE | re.DOTALL,
)
_ASSOCIATION_ITEM_RE = re.compile(r"<(?:li|a)[^>]*>(.*?)</(?:li|a)>", re.IGNORECASE | re.DOTALL)


def _association_values(html: str, kind: str) -> list[str]:
    values: list[str] = []
    for assoc_kind, content in _ASSOCIATION_RE.findall(html):
        if assoc_kind.lower() != kind:
            continue
        for item in _ASSOCIATION_ITEM_RE.findall(content):
            text = clean_text(item)
            if text and text not in values:
                values.append(text)
    return values


def _location_of(html: str) -> str | None:
    location = ", ".join(_association_values(html, "location"))
    if location and len(location) > MAX_LOCATION_LENGTH:
        location = location[: MAX_LOCATION_LENGTH - 3] + "..."
    return location or None


def _workplace_type_of(location: str | None) -> str:
    # The sidebar never states remote/hybrid/onsite explicitly — "virtual"
    # showing up in the location text itself is the only on-page signal
    # observed; a plain city/state/country location implies onsite.
    if location is None:
        return WorkplaceType.UNKNOWN
    return WorkplaceType.REMOTE if "virtual" in location.lower() else WorkplaceType.ONSITE


def _description_of(html: str) -> str | None:
    body_start = html.find('id="job-detail-body"')
    if body_start == -1:
        return None
    body_end = html.find('class="sidebar"', body_start)
    body_html = html[body_start : body_end if body_end != -1 else len(html)]
    # The cut above lands mid-attribute inside the sidebar's opening <div
    # tag, leaving a dangling unclosed "<div " that html_to_formatted_text
    # can't strip (its tag regex requires a closing ">") and that would
    # otherwise leak into the rendered description as literal text.
    if body_html.rfind("<") > body_html.rfind(">"):
        body_html = body_html[: body_html.rfind("<")]

    headings = list(_SECTION_RE.finditer(body_html))
    if not headings:
        return None

    sections = []
    for i, heading in enumerate(headings):
        title = clean_text(heading.group(1))
        if not title:
            continue
        end = headings[i + 1].start() if i + 1 < len(headings) else len(body_html)
        sections.append(f"<h3>{title}</h3>" + body_html[heading.end() : end])
    combined = "".join(sections)
    return html_to_formatted_text(combined) if combined else None


def extract(url: str, html: str) -> ExtractedJobFields | None:
    # company_name is gated on the URL (same as the crawl-side _match
    # above) rather than the HTML markers below — those two signals are
    # independent, matching how job_scanner.py's generic
    # _single_company_name_for_url table (URL-only) and this module's own
    # location/description scrape (HTML-only) never gated on each other.
    is_amazon = bool(_AMAZON_URL_RE.search(url))
    description = _description_of(html)
    location = _location_of(html)
    if not is_amazon and description is None and location is None:
        return None
    return ExtractedJobFields(
        description=description,
        company_name="Amazon" if is_amazon else None,
        location=location,
        workplace_type=_workplace_type_of(location),
    )


def scan_job_url(url: str) -> ScanResult | None:
    if not _AMAZON_URL_RE.search(url):
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
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=base.og_title(html) or base.fallback_title(html),
        description=description,
        company_name="Amazon",
        location=fields.location if fields else None,
        workplace_type=fields.workplace_type if fields else WorkplaceType.UNKNOWN,
        employment_type=EmploymentType.UNKNOWN,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.AMAZON,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://www.amazon.jobs",
    scan_job_url=scan_job_url,
)
