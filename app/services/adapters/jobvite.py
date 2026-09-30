import re

import httpx

from app.models.enums import AtsType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry, limit_job_urls
from app.services.adapters.text import clean_text, extract_balanced_div, html_to_formatted_text

_JOBVITE_JOBS_URL = "https://jobs.jobvite.com/{board_key}/jobs"
_JOBVITE_JOB_URL = "https://jobs.jobvite.com/{board_key}/job/{job_id}"
# careers.jobvite.com is a separate, asset-only host (tenant logos/banner
# images) — the actual board always lives on jobs.jobvite.com, verified live
# against Torrid's board.
_JOBVITE_URL_RE = re.compile(r"jobs\.jobvite\.com/([a-zA-Z0-9_-]+)", re.IGNORECASE)
_TITLE_TAG_RE = re.compile(r"<h2 class=\"jv-header\">(.*?)</h2>", re.IGNORECASE | re.DOTALL)
_META_RE = re.compile(r'<p class="jv-job-detail-meta">(.*?)</p>', re.IGNORECASE | re.DOTALL)
_META_SEPARATOR_RE = re.compile(r"<span[^>]*jv-inline-separator[^>]*>\s*</span>", re.IGNORECASE)
_DESCRIPTION_MARKER = 'class="jv-job-detail-description"'
# Every tenant's board/job <title> follows Jobvite's own template,
# "{Company} Careers" or "{Company} Careers - {Job Title}" — verified live
# against Torrid ("Torrid Careers - Equipment Operator 1st Shift"). No
# og:site_name or other structured company-name source exists on the page.
_COMPANY_NAME_RE = re.compile(r"<title>\s*(.*?)\s+Careers\b", re.IGNORECASE)


def _match(url: str) -> str | None:
    match = _JOBVITE_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    # No public jobs API found; the /jobs listing page server-renders every
    # open role across every category into one page (verified live: Torrid's
    # 40 jobs across 9 categories, no "load more"/pagination control at all).
    response = get_with_retry(_JOBVITE_JOBS_URL.format(board_key=board_key), timeout=TIMEOUT)
    response.raise_for_status()
    job_id_re = re.compile(rf'href="/{re.escape(board_key)}/job/([a-zA-Z0-9]+)"')
    job_ids = dict.fromkeys(job_id_re.findall(response.text))
    return limit_job_urls(_JOBVITE_JOB_URL.format(board_key=board_key, job_id=job_id) for job_id in job_ids)


def _location_of(html: str) -> str | None:
    # jv-job-detail-meta packs "Category<separator>Location" into one <p> —
    # the separator is only present when a location exists at all (verified
    # live: category-only postings would otherwise be misread as a location).
    meta_match = _META_RE.search(html)
    if meta_match is None:
        return None
    parts = _META_SEPARATOR_RE.split(meta_match.group(1), maxsplit=1)
    if len(parts) != 2:
        return None
    return clean_text(parts[1])


def _description_of(html: str) -> str | None:
    marker = html.find(_DESCRIPTION_MARKER)
    if marker == -1:
        return None
    div_start = html.rfind("<div", 0, marker)
    if div_start == -1:
        return None
    inner = extract_balanced_div(html, div_start)
    return html_to_formatted_text(inner) if inner else None


def _company_name_of(html: str) -> str | None:
    match = _COMPANY_NAME_RE.search(html)
    return clean_text(match.group(1)) if match else None


def scan_job_url(url: str) -> ScanResult | None:
    if _match(url) is None:
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    html = page.text

    title_match = _TITLE_TAG_RE.search(html)
    description = _description_of(html)
    salary_min, salary_max, salary_currency = base.salary_from_text(description)

    return ScanResult(
        success=True,
        title=(clean_text(title_match.group(1)) if title_match else None) or base.fallback_title(html),
        description=description or base.fallback_description(html),
        company_name=_company_name_of(html),
        location=_location_of(html),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.JOBVITE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://jobs.jobvite.com/{key}/jobs",
    scan_job_url=scan_job_url,
)
