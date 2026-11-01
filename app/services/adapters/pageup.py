import re

import httpx

from app.models.enums import AtsType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import (
    TIMEOUT,
    AtsAdapter,
    ScanResult,
    get_with_retry,
    limit_job_urls,
    require_listing_page,
)
from app.services.adapters.text import clean_text, extract_balanced_div, html_to_formatted_text

# A PageUp board is careers.pageuppeople.com/{instance id}/{site}, e.g.
# /858/studentjobs, which redirects to its /en-us/listing/ page (verified
# 2026-11-01, University of Delaware: 17 /job/{id}/{slug} links on one page).
# robots.txt only disallows admin and test paths. Job pages have no JSON-LD:
# the title is og:title, the employer og:site_name, the location a
# <span class="location"> and the posting the #job-details div.
_BOARD_RE = re.compile(r"careers\.pageuppeople\.com/(\d+)/([A-Za-z0-9_-]+)", re.IGNORECASE)
_JOB_URL_RE = re.compile(r"careers\.pageuppeople\.com/\d+/[A-Za-z0-9_-]+/[a-z]{2}(?:-[a-z]{2})?/job/\d+", re.IGNORECASE)
_LOCATION_RE = re.compile(r'<span class="location">(.*?)</span>', re.IGNORECASE | re.DOTALL)
_DETAILS_MARKER = 'id="job-details"'


def _match(url: str) -> str | None:
    match = _BOARD_RE.search(url)
    return f"{match.group(1)}/{match.group(2)}" if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    response = get_with_retry(f"https://careers.pageuppeople.com/{board_key}", timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    job_href_re = rf'href="(/{re.escape(board_key)}/[a-z]{{2}}(?:-[a-z]{{2}})?/job/\d+[^"?#]*)"'
    paths = list(dict.fromkeys(re.findall(job_href_re, response.text)))
    if not paths:
        require_listing_page(response.text, "pageup", f"PageUp board {board_key!r}")
    return limit_job_urls(f"https://careers.pageuppeople.com{path}" for path in paths)


def scan_job_url(url: str) -> ScanResult | None:
    if not _JOB_URL_RE.search(url):
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    html = page.text
    description = None
    marker = html.find(_DETAILS_MARKER)
    if marker != -1:
        div_start = html.rfind("<div", 0, marker)
        inner = extract_balanced_div(html, div_start) if div_start != -1 else None
        description = html_to_formatted_text(inner) if inner else None
    description = description or base.fallback_description(html)
    location_match = _LOCATION_RE.search(html)
    location = clean_text(location_match.group(1)) if location_match else None
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=base.og_title(html) or base.fallback_title(html),
        description=description,
        company_name=base.og_site_name(html),
        location=location,
        workplace_type=WorkplaceType.REMOTE if (location or "").strip().lower() == "remote" else WorkplaceType.UNKNOWN,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


# No to_board_url — the board URL carries the instance id and site and the
# language redirect is the platform's own, so it is stored as submitted.
ADAPTER = AtsAdapter(
    AtsType.PAGEUP,
    match=_match,
    fetch_jobs=_fetch_jobs,
    scan_job_url=scan_job_url,
)
