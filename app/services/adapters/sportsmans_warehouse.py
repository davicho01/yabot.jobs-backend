import re
from datetime import date

from app.models.enums import AtsType, EmploymentType
from app.services.adapters.base import (
    TIMEOUT,
    AtsAdapter,
    ExtractedJobFields,
    ScanResult,
    get_with_retry,
    limit_job_urls,
    parse_month_day_year,
)
from app.services.adapters.text import clean_text, html_to_formatted_text

_HOST = "careers.sportsmans.com"
_URL_RE = re.compile(r"careers\.sportsmans\.com", re.IGNORECASE)
# A custom career module bolted onto their SAP Hybris/Commerce storefront
# (script paths /app/public/career/... — verified live), not any known ATS.
# The listing page paginates 10-per-page by default, but the same page
# accepts a "rows" path segment that returns every open posting in one
# request (verified live: rows/200 returned all 183 open reqs with no
# further pagination needed) — simpler and cheaper than walking pages.
_LISTING_URL = f"https://{_HOST}/career/page/1/rows/500/keywords/All/site/100/state/"
_JOB_LINK_RE = re.compile(r'href="(/career/[a-z0-9-]+/\d+/)"', re.IGNORECASE)
_JOB_URL_RE = re.compile(r"careers\.sportsmans\.com/career/[a-z0-9-]+/(\d+)/?$", re.IGNORECASE)
# Job detail pages carry proper schema.org JobPosting *Microdata* (itemprop
# attributes), not JSON-LD — job_scanner's generic JSON-LD extractor doesn't
# see this, so it needs its own regex extraction (verified live against a
# real posting).
_TITLE_RE = re.compile(r'<span itemprop="title">(.*?)</span>', re.IGNORECASE | re.DOTALL)
_DESCRIPTION_RE = re.compile(r'<span itemprop="description">(.*?)</span>\s*</p>', re.IGNORECASE | re.DOTALL)
_CITY_RE = re.compile(r'<span itemprop="addressLocality">(.*?)</span>', re.IGNORECASE)
_REGION_RE = re.compile(r'<span itemprop="addressRegion">(.*?)</span>', re.IGNORECASE)
_EMPLOYMENT_TYPE_RE = re.compile(r'<meta itemprop="employmentType" content="(.*?)"', re.IGNORECASE)
_DATE_POSTED_RE = re.compile(r'<span itemprop="datePosted">(.*?)</span>', re.IGNORECASE)
_EMPLOYMENT_TYPE_MAP = {
    "full-time": EmploymentType.FULL_TIME,
    "part-time": EmploymentType.PART_TIME,
    "temporary": EmploymentType.TEMPORARY,
    "intern": EmploymentType.INTERNSHIP,
}


def _match(url: str) -> str | None:
    return "sportsmans_warehouse" if _URL_RE.search(url) else None


def _fetch_jobs(board_key: str) -> list[str]:  # noqa: ARG001 - single-company board, fixed key, no key needed
    response = get_with_retry(_LISTING_URL, timeout=TIMEOUT)
    response.raise_for_status()
    links = dict.fromkeys(_JOB_LINK_RE.findall(response.text))
    return limit_job_urls(f"https://{_HOST}{path}" for path in links)


def _posted_at(html: str) -> date | None:
    match = _DATE_POSTED_RE.search(html)
    return parse_month_day_year(match.group(1), month_style="%m/%d/%Y") if match else None


def extract(html: str) -> ExtractedJobFields:
    title_match = _TITLE_RE.search(html)
    description_match = _DESCRIPTION_RE.search(html)
    city_match = _CITY_RE.search(html)
    region_match = _REGION_RE.search(html)
    employment_match = _EMPLOYMENT_TYPE_RE.search(html)

    location_parts = [clean_text(m.group(1)) for m in (city_match, region_match) if m]
    return ExtractedJobFields(
        title=clean_text(title_match.group(1)) if title_match else None,
        description=html_to_formatted_text(description_match.group(1)) if description_match else None,
        company_name="Sportsman's Warehouse",
        location=", ".join(location_parts) if location_parts else None,
        employment_type=_EMPLOYMENT_TYPE_MAP.get(
            employment_match.group(1).lower() if employment_match else "", EmploymentType.UNKNOWN
        ),
        posted_at=_posted_at(html),
    )


def scan_job_url(url: str) -> ScanResult | None:
    if _JOB_URL_RE.search(url) is None:
        return None
    response = get_with_retry(url, timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    fields = extract(response.text)
    if fields.title is None:
        return None
    return ScanResult(
        success=True,
        title=fields.title,
        description=fields.description,
        company_name=fields.company_name,
        location=fields.location,
        employment_type=fields.employment_type,
        posted_at=fields.posted_at,
        raw_html_excerpt=response.text[:20_000],
        full_html=response.text,
    )


# Single-company in-house career site — fixed host, no to_board_url needed,
# same reasoning as amazon.py/walmart.py/bestbuy.py.
ADAPTER = AtsAdapter(
    AtsType.SPORTSMANS_WAREHOUSE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    scan_job_url=scan_job_url,
)
