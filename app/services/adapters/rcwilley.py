import re

import httpx

from app.models.enums import AtsType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry, limit_job_urls
from app.services.adapters.text import clean_text

_RCWILLEY_URL_RE = re.compile(r"rcwilley\.com", re.IGNORECASE)
# isNewQuery=true server-renders every open role on one page (verified live:
# 32 jobs across stores, warehouses and corporate, no pagination). The
# ?department= links next to it are filtered client-side and serve the same
# 7-job "recent" teaser regardless of department, so they're no use here.
_LISTING_URL = "https://www.rcwilley.com/Get-Jobs?isNewQuery=true"
_JOB_HREF_RE = re.compile(r'href="(/job/[^"/]+/\d+/[^"]+)"')
_JOB_PATH_RE = re.compile(r"rcwilley\.com/job/[^/]+/\d+/", re.IGNORECASE)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)


def _match(url: str) -> str | None:
    # Single-company in-house career module on their own storefront (like
    # sportsmans_warehouse.py) — the domain alone is the signal.
    return "rcwilley" if _RCWILLEY_URL_RE.search(url) else None


def _fetch_jobs(_board_key: str) -> list[str]:
    response = get_with_retry(_LISTING_URL, timeout=TIMEOUT)
    response.raise_for_status()
    paths = dict.fromkeys(_JOB_HREF_RE.findall(response.text))
    return limit_job_urls(f"https://www.rcwilley.com{path}" for path in paths)


def scan_job_url(url: str) -> ScanResult | None:
    if not _JOB_PATH_RE.search(url):
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    html = page.text

    postings = base.extract_json_ld_postings(html)
    job_ld = postings[0] if postings else {}
    # The JSON-LD "title" is the department ("Corporate", "Warehouse"), not
    # the role (verified live on /job/Corporate-Office/33871/...) — the
    # page's own <h1> carries the real job title.
    h1 = _H1_RE.search(html)
    salary_min, salary_max, salary_currency = base.job_ld_salary(job_ld)
    description = job_ld.get("description")

    return ScanResult(
        success=True,
        title=(clean_text(h1.group(1)) if h1 else None) or base.fallback_title(html),
        description=description.strip() if isinstance(description, str) else base.fallback_description(html),
        company_name="RC Willey",
        location=base.job_ld_location(job_ld),
        employment_type=base.job_ld_employment_type(job_ld),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=base.job_ld_posted_at(job_ld),
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.RCWILLEY,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://www.rcwilley.com/Jobs",
    scan_job_url=scan_job_url,
)
