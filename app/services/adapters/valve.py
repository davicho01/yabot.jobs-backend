import re

import httpx

from app.models.enums import AtsType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry, limit_job_urls
from app.services.adapters.text import clean_text, extract_balanced_div, html_to_formatted_text

_VALVE_JOBS_URL = "https://www.valvesoftware.com/en/jobs"
_VALVE_JOB_URL = "https://www.valvesoftware.com/en/jobs?job_id={job_id}"
_VALVE_URL_RE = re.compile(r"valvesoftware\.com/(?:[a-z]{2}/)?jobs\b", re.IGNORECASE)
_JOB_ID_RE = re.compile(r"job_id=(\d+)")
_TITLE_RE = re.compile(r"<h1>\s*(.*?)\s*</h1>", re.IGNORECASE | re.DOTALL)
_LOCATION_RE = re.compile(r'<p class="job_opening_location">\s*(.*?)\s*</p>', re.IGNORECASE | re.DOTALL)
_DESCRIPTION_MARKER = 'class="job_description_intro"'
# Every posting's location line is a full sentence — "We work together in
# person, in Bellevue, WA, USA" — rather than a bare place; only the
# trailing "in {place}" is useful for location-based search/filtering
# (verified live: every one of Valve's 26 current postings uses this exact
# "We work together ..., in {place}" phrasing, all in-office in Bellevue).
_LOCATION_PREFIX_RE = re.compile(r"^.*?\bin\s+(?=[A-Z])", re.DOTALL)


def _match(url: str) -> str | None:
    return "valve" if _VALVE_URL_RE.search(url) else None


def _fetch_jobs(_board_key: str) -> list[str]:
    # No public API; the /en/jobs listing server-renders every opening as a
    # plain ?job_id= link (verified live: 26 current postings, no
    # pagination).
    response = get_with_retry(_VALVE_JOBS_URL, timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    job_ids = dict.fromkeys(_JOB_ID_RE.findall(response.text))
    return limit_job_urls(_VALVE_JOB_URL.format(job_id=job_id) for job_id in job_ids)


def _description_of(html: str) -> str | None:
    marker = html.find(_DESCRIPTION_MARKER)
    if marker == -1:
        return None
    div_start = html.rfind("<div", 0, marker)
    if div_start == -1:
        return None
    inner = extract_balanced_div(html, div_start)
    return html_to_formatted_text(inner) if inner else None


def scan_job_url(url: str) -> ScanResult | None:
    if _match(url) is None or "job_id=" not in url:
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    html = page.text

    title_match = _TITLE_RE.search(html)
    location_match = _LOCATION_RE.search(html)
    location = clean_text(location_match.group(1)) if location_match else None
    if location:
        location = _LOCATION_PREFIX_RE.sub("", location, count=1) or location

    return ScanResult(
        success=True,
        title=clean_text(title_match.group(1)) if title_match else base.fallback_title(html),
        description=_description_of(html),
        company_name="Valve",
        location=location,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.VALVE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: _VALVE_JOBS_URL,
    scan_job_url=scan_job_url,
)
