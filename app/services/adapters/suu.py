import re
from datetime import datetime

import httpx

from app.models.enums import AtsType, EmploymentType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry, limit_job_urls
from app.services.adapters.text import clean_text, extract_balanced_div, html_to_formatted_text

# Southern Utah University's in-house student-employment board on its mySUU
# portal (on-campus, work-study and internship roles). SUU's staff/faculty
# postings live on schooljobs.com instead (NEOGOV, not supported).
_SUU_JOBS_RE = re.compile(r"my\.suu\.edu/jobs", re.IGNORECASE)
_JOB_URL_RE = re.compile(r"my\.suu\.edu/jobs/(\d+)", re.IGNORECASE)
_LISTING_URL = "https://my.suu.edu/jobs"
# Listing hrefs carry a trailing space inside the quotes (verified live:
# href="/jobs/55648 ").
_JOB_HREF_RE = re.compile(r'href="/jobs/(\d+)\s*"')
_TITLE_RE = re.compile(r"<h1[^>]*>\s*<span[^>]*>(.*?)</span>", re.IGNORECASE | re.DOTALL)
_DETAIL_RE = re.compile(
    r'<span class="text-body-secondary[^"]*"[^>]*>\s*([^<:]+):\s*</span>\s*<span[^>]*>(.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)
# Closed postings still answer 200 with their full content plus this notice,
# not a 404 (verified live: /jobs/55988).
_CLOSED_MARKER = "This job posting is no longer active"


def _match(url: str) -> str | None:
    return "suu" if _SUU_JOBS_RE.search(url) else None


def _fetch_jobs(_board_key: str) -> list[str]:
    # One server-rendered page lists every open posting (verified live: 40
    # jobs, no pagination control — category filters are client-side).
    response = get_with_retry(_LISTING_URL, timeout=TIMEOUT)
    response.raise_for_status()
    job_ids = dict.fromkeys(_JOB_HREF_RE.findall(response.text))
    return limit_job_urls(f"https://my.suu.edu/jobs/{job_id}" for job_id in job_ids)


def _employment_type(position_type: str | None, category: str | None) -> str:
    label = f"{position_type or ''} {category or ''}".lower()
    if "intern" in label:
        return EmploymentType.INTERNSHIP
    if "full" in label:
        return EmploymentType.FULL_TIME
    if any(word in label for word in ("student", "hourly", "work study", "part")):
        return EmploymentType.PART_TIME
    return EmploymentType.UNKNOWN


def _posted_at(value: str | None):
    try:
        return datetime.strptime(value, "%B %d, %Y").date() if value else None
    except ValueError:
        return None


def _description(html: str) -> str | None:
    start = html.find('<div class="body">')
    inner = extract_balanced_div(html, start) if start != -1 else None
    return html_to_formatted_text(inner) if inner else None


def scan_job_url(url: str) -> ScanResult | None:
    if not _JOB_URL_RE.search(url):
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    html = page.text
    if _CLOSED_MARKER in html:
        return ScanResult(success=False, error="SUU marks this posting as no longer active.", expired=True)

    details = {clean_text(label): clean_text(value) for label, value in _DETAIL_RE.findall(html)}
    title_match = _TITLE_RE.search(html)
    salary_min, salary_max, salary_currency = base.salary_from_text(details.get("Wage"))
    return ScanResult(
        success=True,
        title=(clean_text(title_match.group(1)) if title_match else None) or base.fallback_title(html),
        description=_description(html) or base.fallback_description(html),
        company_name="Southern Utah University",
        location=details.get("Location") or "Cedar City, UT",
        employment_type=_employment_type(details.get("Position Type"), details.get("Category")),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=_posted_at(details.get("Posted on")),
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.SUU,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: _LISTING_URL,
    scan_job_url=scan_job_url,
)
