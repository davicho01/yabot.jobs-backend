from typing import Any
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType, EmploymentType
from app.services.adapters import base
from app.services.adapters.base import (
    DEFAULT_MAX_JOBS_PER_CRAWL,
    TIMEOUT,
    AtsAdapter,
    ExtractedJobFields,
    ScanResult,
    parse_month_day_year,
    post_with_retry,
)

_ECHO_JOBS_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
# "Echo Jobs" (by Dorian Solutions Group) is a self-hosted WordPress plugin,
# not a shared multi-tenant host — every tenant runs on its own domain, same
# as Clinch/Oracle Fusion, so there's no static URL shape to match() on.
_ECHO_JOBS_SIGNATURE = "wp-content/plugins/echo-jobs"
_GO_PHP_URL = "https://{host}/wp-content/plugins/echo-jobs/go.php"
# The plugin's job custom-post-type slug, verified live (profocustechnology.com
# permalinks are .../echojobs/{title}-{external_id}/) — a cheap pre-check so
# scan_job_url (tried against every submitted URL regardless of platform) only
# ever calls out to a host's go.php for URLs that actually look like this
# plugin's job pages.
_JOB_URL_PATH_SIGNATURE = "/echojobs/"
# profocustechnology.com (verified live) sits behind a WAF that 403s a plain
# httpx request (its default "python-httpx/..." UA) on both the homepage and
# go.php itself, but passes any recognizable UA string, including this one —
# no browser-render fallback needed.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; YabotJobsBot/1.0)"}


def _get_jobs(host: str) -> list[dict[str, Any]]:
    response = post_with_retry(
        _GO_PHP_URL.format(host=host),
        data={"type": "getJobs"},
        headers=_HEADERS,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    jobs = response.json()
    return jobs if isinstance(jobs, list) else []


def _fetch_jobs(host: str) -> list[str]:
    urls = [job["permalink"] for job in _get_jobs(host) if isinstance(job.get("permalink"), str)]
    return urls[:_ECHO_JOBS_MAX_JOBS]


def _detect_embedded(url: str) -> str | None:
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True, headers=_HEADERS)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    host = urlsplit(str(response.url)).netloc
    if _ECHO_JOBS_SIGNATURE in response.text:
        return host
    # Mirrors clinch.py: a plain fetch of the marketing/careers page can miss
    # the signature (differently-themed page, the plugin loaded only on the
    # jobs page itself) even though the plugin's own JSON API still responds
    # — a non-empty job list is just as strong a signal.
    try:
        return host if _fetch_jobs(host) else None
    except (httpx.HTTPError, ValueError):
        return None


def _board_key(url: str) -> str | None:
    return urlsplit(url).netloc or None


def _find_job(jobs: list[dict[str, Any]], url: str) -> dict[str, Any] | None:
    target = url.rstrip("/")
    for job in jobs:
        permalink = job.get("permalink")
        if isinstance(permalink, str) and permalink.rstrip("/") == target:
            return job
    return None


# "Direct Hire"/"Contract"/"Contract To Hire" verified live against a real
# tenant (profocustechnology.com, an IT staffing agency's board) — other
# free-text values a different tenant's admin might type into this
# self-hosted plugin fall back to UNKNOWN like every other adapter's
# unmapped case.
_JOB_TYPE_MAP = {
    "direct hire": EmploymentType.FULL_TIME,
    "full time": EmploymentType.FULL_TIME,
    "full-time": EmploymentType.FULL_TIME,
    "part time": EmploymentType.PART_TIME,
    "part-time": EmploymentType.PART_TIME,
    "contract": EmploymentType.CONTRACT,
    "contract to hire": EmploymentType.CONTRACT,
    "temporary": EmploymentType.TEMPORARY,
    "intern": EmploymentType.INTERNSHIP,
    "internship": EmploymentType.INTERNSHIP,
}


def _employment_type_of(job: dict[str, Any]) -> str:
    job_type = job.get("type")
    if isinstance(job_type, str) and job_type:
        return _JOB_TYPE_MAP.get(job_type.strip().lower(), EmploymentType.UNKNOWN)
    return EmploymentType.UNKNOWN


def _location_of(job: dict[str, Any]) -> str | None:
    parts = [p.strip() for p in (job.get("city"), job.get("state")) if isinstance(p, str) and p.strip()]
    return ", ".join(parts) or None


def extract(url: str, _html: str) -> ExtractedJobFields | None:
    host = urlsplit(url).netloc
    try:
        jobs = _get_jobs(host)
    except (httpx.HTTPError, ValueError):
        return None
    job = _find_job(jobs, url)
    if job is None:
        return None
    title = job.get("title")
    description = job.get("description")
    return ExtractedJobFields(
        title=title if isinstance(title, str) else None,
        description=base.html_to_formatted_text(description) if isinstance(description, str) else None,
        location=_location_of(job),
        employment_type=_employment_type_of(job),
        posted_at=parse_month_day_year(job.get("updated_at"), month_style="%B %d, %Y"),
    )


# Job pages carry no JobPosting JSON-LD and no meta/og:description at all
# (verified live) — just a bare og:title/og:site_name — so the generic
# default scanner alone would surface almost nothing. The plugin's own
# getJobs API (same one _fetch_jobs reads) already returns the full posting
# record, keyed by exact permalink match.
def scan_job_url(url: str) -> ScanResult | None:
    if _JOB_URL_PATH_SIGNATURE not in urlsplit(url).path:
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
        title=(fields.title if fields else None) or base.og_title(html) or base.fallback_title(html),
        description=description,
        company_name=base.og_site_name(html),
        location=fields.location if fields else None,
        employment_type=fields.employment_type if fields else EmploymentType.UNKNOWN,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=fields.posted_at if fields else None,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


# No to_board_url — board_url is stored verbatim, exactly as submitted,
# same reasoning as Oracle Fusion/Clinch.
ADAPTER = AtsAdapter(
    AtsType.ECHO_JOBS,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
    embedded_match=_detect_embedded,
    scan_job_url=scan_job_url,
)
