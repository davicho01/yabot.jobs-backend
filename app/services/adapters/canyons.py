import json
import re

import httpx

from app.models.enums import AtsType, EmploymentType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry, limit_job_urls
from app.services.adapters.text import clean_text, extract_balanced_div, html_to_formatted_text

# Canyons School District's in-house ColdFusion job board. The listing page
# (hr/home.cfm) is a server-side DataTables widget whose rows come from
# server_home.cfm as JSON; detail pages (viewjob.cfm?jid=) are plain HTML.
_CANYONS_RE = re.compile(r"jobs\.canyonsdistrict\.org", re.IGNORECASE)
_JOB_URL_RE = re.compile(r"jobs\.canyonsdistrict\.org/hr/viewjob\.cfm\?jid=(\d+)", re.IGNORECASE)
_BOARD_URL = "https://jobs.canyonsdistrict.org/hr/home.cfm"
_DATA_URL = "https://jobs.canyonsdistrict.org/hr/server_home.cfm"
_JOB_URL = "https://jobs.canyonsdistrict.org/hr/viewjob.cfm?jid={job_id}"
# The DataTables endpoint answers an empty body unless every filter is
# present; 0 means "all" for each (verified live: cat=0 returns 22 rows, a
# superset of every individual category).
_DATA_PARAMS = {
    "jf": 0, "cat": 0, "loc": 0, "jt": 0,
    "sEcho": 1, "iColumns": 7, "iDisplayStart": 0, "iDisplayLength": 500,
    "iSortCol_0": 4, "sSortDir_0": "asc", "iSortingCols": 1,
}
# The server 404s httpx's default python-httpx User-Agent on every path
# (verified live) but serves the same UA base.fetch_html already sends.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; YabotJobsBot/1.0)"}
_JID_RE = re.compile(r"viewjob\.cfm\?jid=(\d+)")
_TITLE_RE = re.compile(r"<h1>\s*<b>(.*?)</b>\s*</h1>", re.IGNORECASE | re.DOTALL)
_FIELD_RE = re.compile(r"<b>\s*([^<:]+):\s*</b>\s*</td>\s*<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
# Closed or unknown jids redirect to a login-wall page instead of a 404
# (verified live: jid=1).
_LOGIN_WALL_MARKER = "Login Required"


def _match(url: str) -> str | None:
    return "canyons" if _CANYONS_RE.search(url) else None


def _fetch_jobs(_board_key: str) -> list[str]:
    response = get_with_retry(_DATA_URL, params=_DATA_PARAMS, headers=_HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    # Rows hold raw tabs/newlines inside strings, so strict JSON parsing fails.
    rows = json.loads(response.text, strict=False).get("aaData") or []
    job_ids = dict.fromkeys(match.group(1) for row in rows if (match := _JID_RE.search(" ".join(map(str, row)))))
    return limit_job_urls(_JOB_URL.format(job_id=job_id) for job_id in job_ids)


def _employment_type(job_type: str | None) -> str:
    label = (job_type or "").lower()
    if "full" in label:
        return EmploymentType.FULL_TIME
    if "part" in label:
        return EmploymentType.PART_TIME
    if "temp" in label or "substitute" in label:
        return EmploymentType.TEMPORARY
    return EmploymentType.UNKNOWN


def scan_job_url(url: str) -> ScanResult | None:
    if not _JOB_URL_RE.search(url):
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    html = page.text
    title_match = _TITLE_RE.search(html)
    if title_match is None and _LOGIN_WALL_MARKER in html:
        return ScanResult(success=False, error="Canyons no longer lists this posting.", expired=True)

    fields = {clean_text(label): clean_text(value) for label, value in _FIELD_RE.findall(html)}
    body_start = html.find('<div id="example2">')
    body = extract_balanced_div(html, body_start) if body_start != -1 else None
    # "Location" is a school or program name ("Early Childhood", "Alta
    # High"), not a place (it stays visible in the description) — every
    # school sits within the district's Salt Lake Valley boundaries, so the
    # district office's city stands in for it.
    return ScanResult(
        success=True,
        title=(clean_text(title_match.group(1)) if title_match else None) or base.fallback_title(html),
        description=html_to_formatted_text(body) if body else base.fallback_description(html),
        company_name="Canyons School District",
        location="Sandy, UT",
        employment_type=_employment_type(fields.get("Job Type")),
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.CANYONS,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: _BOARD_URL,
    scan_job_url=scan_job_url,
)
