import html as html_lib
import re
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry, limit_job_urls
from app.services.adapters.text import MAX_LOCATION_LENGTH, clean_text, html_to_formatted_text

# SmartRecruiters — a multi-tenant ATS. A company's public career site is
# careers.smartrecruiters.com/{company} and each job is
# jobs.smartrecruiters.com/{company}/{postingId}-{slug}. Its public JSON API
# (api.smartrecruiters.com) is off limits: that host's robots.txt disallows
# every crawler but LinkedIn's (verified 2026-10-06). The career site and
# job pages have no robots.txt restrictions, so listing and scanning use
# those instead: the career site's own "Show more jobs" endpoint
# (/{company}/api/more?page=N, HTML fragments of 24 jobs, until a page adds
# none — verified to return every posting: 332/332 for Western Digital),
# and each job page's schema.org microdata.
_CAREERS_HOST = "careers.smartrecruiters.com"
_JOBS_HOST = "jobs.smartrecruiters.com"
_JOIN_HOST = "join.smartrecruiters.com"
# jobs.smartrecruiters.com paths whose first segment isn't a company.
_RESERVED_SEGMENTS = {"my-applications", "oneclick-ui", "sr-jobs", "api", "static", "ni", "public"}
_KEY_RE = re.compile(r"[A-Za-z0-9_-]+")
_JOB_PATH_RE = re.compile(r"^/([A-Za-z0-9_-]+)/(\d{6,})(?:-[^/?#]*)?/?$")
# A posting that's been filled or removed answers 400 (verified live with a
# made-up id); 404/410 for good measure.
_GONE_STATUSES = {400, 404, 410}
_MAX_PAGES = 100

_TITLE_RE = re.compile(r'itemprop="title"[^>]*>(.*?)</h1>', re.IGNORECASE | re.DOTALL)
_LOCATION_RE = re.compile(r'<spl-job-location\b([^>]*)>', re.IGNORECASE)
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
_EMPLOYMENT_RE = re.compile(r'itemprop="employmentType"[^>]*>(.*?)<', re.IGNORECASE | re.DOTALL)
_ORG_NAME_RE = re.compile(
    r'itemprop="hiringOrganization".*?<meta itemprop="name" content="([^"]*)"', re.IGNORECASE | re.DOTALL
)
_DATE_POSTED_RE = re.compile(r'<meta itemprop="datePosted" content="([^"]*)"', re.IGNORECASE)
_DESCRIPTION_START_RE = re.compile(r'<div itemprop="description">', re.IGNORECASE)
_COUNTRY_RE = re.compile(r'<meta itemprop="addressCountry" content="([^"]*)"', re.IGNORECASE)
# A company's own "Salary Range" field, among the page's job details, with no
# currency ("Salary Range: 124,000.00-165,300.00", verified live on a US
# Western Digital posting) — read as USD for a US job only.
_SALARY_DETAIL_RE = re.compile(
    r'class="job-detail">\s*(?:Salary|Pay|Compensation)[^:<]*:\s*([\d,.]+)\s*[-–]\s*([\d,.]+)\s*<', re.IGNORECASE
)
_US = {"united states", "us", "usa"}

_EMPLOYMENT_TYPES = {
    "full-time": EmploymentType.FULL_TIME,
    "part-time": EmploymentType.PART_TIME,
    "contract": EmploymentType.CONTRACT,
    "contractor": EmploymentType.CONTRACT,
    "temporary": EmploymentType.TEMPORARY,
    "internship": EmploymentType.INTERNSHIP,
    "intern": EmploymentType.INTERNSHIP,
}
_WORKPLACE_TYPES = {"on_site": WorkplaceType.ONSITE, "remote": WorkplaceType.REMOTE, "hybrid": WorkplaceType.HYBRID}


def _company_segment(path: str, host: str) -> str | None:
    segments = [s for s in path.split("/") if s]
    if host == _JOBS_HOST and segments[:1] == ["my-applications"]:
        segments = segments[1:]  # jobs.smartrecruiters.com/my-applications/{company}
    if not segments or segments[0].lower() in _RESERVED_SEGMENTS:
        return None
    return segments[0] if _KEY_RE.fullmatch(segments[0]) else None


def _match(url: str) -> str | None:
    # Company identifiers are case-insensitive on the career site
    # (careers.smartrecruiters.com/westerndigital works), so one key per
    # company regardless of how a link spells it.
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host not in (_CAREERS_HOST, _JOBS_HOST, _JOIN_HOST):
        return None
    company = _company_segment(parts.path, host)
    return company.lower() if company else None


def _board_url(board_key: str) -> str:
    return f"https://{_CAREERS_HOST}/{board_key}"


def _fetch_jobs(board_key: str) -> list[str]:
    job_link = re.compile(rf'href="(https://{re.escape(_JOBS_HOST)}/[A-Za-z0-9_-]+/\d+[^"]*)"')
    urls: list[str] = []
    for page in range(_MAX_PAGES):
        response = get_with_retry(f"{_board_url(board_key)}/api/more", params={"page": page}, timeout=TIMEOUT)
        response.raise_for_status()  # an unknown company answers 404 here
        new = [u for u in dict.fromkeys(html_lib.unescape(u) for u in job_link.findall(response.text)) if u not in urls]
        if not new:
            break
        urls += new
    return limit_job_urls(urls)


def _place(html: str) -> str | None:
    """"City, Region, Country" from the address microdata, each part once."""
    parts: list[str] = []
    for prop in ("addressLocality", "addressRegion", "addressCountry"):
        match = re.search(rf'<meta itemprop="{prop}" content="([^"]*)"', html, re.IGNORECASE)
        value = clean_text(html_lib.unescape(match.group(1))) if match else None
        if value and value.lower() not in (p.lower() for p in parts):
            parts.append(value.title() if value.isupper() and len(value) > 3 else value)
    return ", ".join(parts) or None


def _location(html: str) -> tuple[str | None, str]:
    match = _LOCATION_RE.search(html)
    if not match:
        return None, WorkplaceType.UNKNOWN
    attrs = {k.lower(): html_lib.unescape(v) for k, v in _ATTR_RE.findall(match.group(1))}
    location = clean_text(attrs.get("formattedaddress"))
    if location and re.search(r"\d", location):
        # A street address ("30 Rockefeller Plaza, New York, NEW YORK"): the
        # place itself from the address microdata instead.
        location = _place(html) or location
    if location and len(location) > MAX_LOCATION_LENGTH:
        location = location[: MAX_LOCATION_LENGTH - 3] + "..."
    return location, _WORKPLACE_TYPES.get(attrs.get("workplacetype", "").lower(), WorkplaceType.UNKNOWN)


def _description(html: str) -> str | None:
    start = _DESCRIPTION_START_RE.search(html)
    if not start:
        return None
    # The description div holds one <section class="job-section"> per part
    # (company, job description, qualifications, additional information).
    depth, i = 1, start.end()
    for tag in re.finditer(r"<(/?)div\b[^>]*>", html[i:], re.IGNORECASE):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return html_to_formatted_text(html[i : i + tag.start()])
    return None


def _salary(html: str, description: str | None) -> tuple[int | None, int | None, str | None]:
    found = base.salary_from_text(description)
    if found[0] is not None:
        return found
    detail = _SALARY_DETAIL_RE.search(html)
    country = _COUNTRY_RE.search(html)
    if not detail or not country or country.group(1).strip().lower() not in _US:
        return None, None, None
    try:
        low, high = (int(float(v.replace(",", ""))) for v in detail.groups())
    except ValueError:
        return None, None, None
    return (low, high, "USD") if 0 < low <= high else (None, None, None)


def _fetch_page(url: str) -> base.FetchedPage | ScanResult:
    """The job page, or a failed ScanResult. A direct request first, so a
    removed posting's 400 is seen as such — fetch_html's browser fallback
    would otherwise render the error page as if it were the job."""
    try:
        response = get_with_retry(url, timeout=10.0, follow_redirects=True)
        if response.status_code in _GONE_STATUSES:
            return ScanResult(success=False, error="SmartRecruiters no longer lists this job.", expired=True)
        response.raise_for_status()
        return base.FetchedPage(text=response.text, url=str(response.url))
    except httpx.HTTPError:
        try:
            return base.fetch_html(url)
        except httpx.HTTPError as exc:
            return ScanResult(success=False, error=str(exc))


def scan_job_url(url: str) -> ScanResult | None:
    parts = urlsplit(url)
    if (parts.hostname or "").lower() != _JOBS_HOST or not _JOB_PATH_RE.match(parts.path):
        return None
    page = _fetch_page(url)
    if isinstance(page, ScanResult):
        return page

    html = page.text
    title_match = _TITLE_RE.search(html)
    title = clean_text(re.sub(r"<[^>]+>", " ", title_match.group(1))) if title_match else base.og_title(html)
    if not title:
        return ScanResult(success=False, error="SmartRecruiters job page has no title.")
    location, workplace_type = _location(html)
    employment = _EMPLOYMENT_RE.search(html)
    org = _ORG_NAME_RE.search(html)
    posted = _DATE_POSTED_RE.search(html)
    description = _description(html)
    salary_min, salary_max, salary_currency = _salary(html, description)
    return ScanResult(
        success=True,
        title=title,
        description=description,
        company_name=clean_text(html_lib.unescape(org.group(1))) if org else base.og_site_name(html),
        location=location,
        workplace_type=workplace_type,
        employment_type=_EMPLOYMENT_TYPES.get((clean_text(employment.group(1)) or "").lower(), EmploymentType.UNKNOWN)
        if employment
        else EmploymentType.UNKNOWN,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=base.posting_date(posted.group(1)) if posted else None,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.SMARTRECRUITERS,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=_board_url,
    scan_job_url=scan_job_url,
)
