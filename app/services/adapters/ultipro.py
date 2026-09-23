import re
from datetime import date
from urllib.parse import urljoin

from app.models.enums import AtsType, EmploymentType
from app.services.adapters.base import AtsAdapter, ExtractedJobFields, ScanResult, limit_job_urls
from app.services.browser_fetch import fetch_rendered_html
from app.services.adapters.text import clean_text, html_to_formatted_text

# UltiPro/UKG Pro's "Ignite" recruiting portal — multi-tenant at
# recruiting[2].ultipro.com/{tenant}/JobBoard/{boardId}/, tenant identified
# by an internal code (e.g. ALS1001ALSCO), boardId a GUID. The whole
# listing and job-detail UI renders inside Web Component shadow roots
# (verified live against 4 real tenants) — a plain fetch_rendered_html
# comes back with an empty custom-element shell no matter how long you
# wait, since page.content() never serializes shadow DOM. The board
# listing's job links happen to sit in the light DOM regardless (verified:
# 90 <a href> links present with no shadow-piercing needed), but every
# field on the job-detail page (title, location, description, ...) is
# shadow-only, hence pierce_shadow=True only on the scan_job_url path.
_TENANT_RE = re.compile(r"(recruiting\d*\.ultipro\.com)/([^/]+)/JobBoard/([^/?]+)", re.IGNORECASE)
_BOARD_URL = "https://{host}/{tenant}/JobBoard/{board_id}/"
_JOB_LINK_RE = re.compile(r'href="([^"]*OpportunityDetail\?opportunityId=[a-f0-9-]+)"', re.IGNORECASE)
_JOB_DETAIL_RE = re.compile(r"OpportunityDetail\?opportunityId=", re.IGNORECASE)
# Ignite tags every field with a stable data-automation attribute
# (verified live — presumably for UltiPro's own QA suite), far more
# reliable to key off than its generated class names.
_TITLE_RE = re.compile(r'data-automation="opportunity-title"[^>]*>(.*?)</span>', re.IGNORECASE | re.DOTALL)
_LOCATION_RE = re.compile(
    r'data-automation="city-state-zip-country-label"[^>]*>(.*?)</span>', re.IGNORECASE | re.DOTALL
)
_DESCRIPTION_START_RE = re.compile(r'<p\b[^>]*data-automation="job-description"[^>]*>', re.IGNORECASE)
_POSTED_DATE_RE = re.compile(r'data-automation="job-posted-date"[^>]*>(.*?)</span>', re.IGNORECASE | re.DOTALL)
# The attribute *name* itself varies by schedule type (verified live:
# "JobFullTime"); the visible text is the reliable part to key off.
_SCHEDULE_RE = re.compile(r'data-automation="Job[A-Za-z]+"[^>]*>(.*?)</span>', re.IGNORECASE | re.DOTALL)
_EMPLOYMENT_TYPE_MAP = {
    "full-time": EmploymentType.FULL_TIME,
    "part-time": EmploymentType.PART_TIME,
    "temporary": EmploymentType.TEMPORARY,
    "intern": EmploymentType.INTERNSHIP,
}
_MONTHS = (
    "January February March April May June July August September October November December"
).split()


def _match(url: str) -> str | None:
    match = _TENANT_RE.search(url)
    return "/".join(match.groups()) if match else None


def _board_url(board_key: str) -> str:
    host, tenant, board_id = board_key.split("/")
    return _BOARD_URL.format(host=host, tenant=tenant, board_id=board_id)


def _board_key(url: str) -> str | None:
    return _match(url)


def _fetch_jobs(board_key: str) -> list[str]:
    board_url = _board_url(board_key)
    html = fetch_rendered_html(board_url, wait_for_selector='a[href*="OpportunityDetail"]')
    if html is None:
        return []
    host = board_key.split("/")[0]
    links = dict.fromkeys(_JOB_LINK_RE.findall(html))
    return limit_job_urls(urljoin(f"https://{host}", link) for link in links)


def _extract_balanced_p(html: str, open_tag_end: int) -> str | None:
    # Same reasoning as base.extract_balanced_div (a non-greedy regex stops
    # at the description's own first nested </p>, truncating everything
    # after) — the description body is itself made of several <p>/<ul>
    # blocks (verified live), so this has to track real nesting depth.
    depth = 1
    for m in re.finditer(r"<(/?)p\b[^>]*>", html[open_tag_end:], re.IGNORECASE):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return html[open_tag_end : open_tag_end + m.start()]
    return None


def _posted_at(text: str | None):
    if not text:
        return None
    match = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", clean_text(text) or "")
    if not match:
        return None
    month_name, day, year = match.groups()
    try:
        month = _MONTHS.index(month_name) + 1
    except ValueError:
        return None
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def extract(html: str) -> ExtractedJobFields:
    title_match = _TITLE_RE.search(html)
    location_match = _LOCATION_RE.search(html)
    schedule_match = _SCHEDULE_RE.search(html)
    posted_match = _POSTED_DATE_RE.search(html)

    description = None
    desc_start = _DESCRIPTION_START_RE.search(html)
    if desc_start:
        inner = _extract_balanced_p(html, desc_start.end())
        description = html_to_formatted_text(inner) if inner else None

    schedule_text = clean_text(schedule_match.group(1)) if schedule_match else None
    return ExtractedJobFields(
        title=clean_text(title_match.group(1)) if title_match else None,
        description=description,
        location=clean_text(location_match.group(1)) if location_match else None,
        employment_type=_EMPLOYMENT_TYPE_MAP.get((schedule_text or "").lower(), EmploymentType.UNKNOWN),
        posted_at=_posted_at(posted_match.group(1) if posted_match else None),
    )


def scan_job_url(url: str) -> ScanResult | None:
    if _match(url) is None or not _JOB_DETAIL_RE.search(url):
        return None
    html = fetch_rendered_html(url, wait_for_selector='[data-automation="job-description"]', pierce_shadow=True)
    if html is None:
        return ScanResult(success=False, error=f"Failed to render {url} (browser fetch unavailable or timed out)")
    fields = extract(html)
    if fields.title is None:
        return None
    return ScanResult(
        success=True,
        title=fields.title,
        description=fields.description,
        location=fields.location,
        employment_type=fields.employment_type,
        posted_at=fields.posted_at,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


# No to_board_url canonicalization beyond what _board_url already does —
# board_key round-trips cleanly through the shared host/tenant/boardId
# shape, same reasoning as taleo.py's tenant/section split.
ADAPTER = AtsAdapter(
    AtsType.ULTIPRO,
    match=_match,
    board_key=_board_key,
    fetch_jobs=_fetch_jobs,
    to_board_url=_board_url,
    scan_job_url=scan_job_url,
)
