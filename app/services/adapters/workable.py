import functools
import re

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, limit_job_urls, AtsAdapter, ScanResult, get_with_retry
from app.services.adapters.text import clean_text, html_to_formatted_text

_WORKABLE_JOBS_URL = "https://apply.workable.com/api/v1/widget/accounts/{board_key}"
# One job's full posting. Job pages themselves are a JavaScript app whose
# HTML only carries the og:description teaser (~250 chars) — verified live
# 2026-10-12, which is all the generic scanner ever got for Workable jobs.
# This public API returns the whole posting: description, requirements,
# benefits, structured location/type/workplace and the published date. A
# removed job (or wrong account) is a 404.
_WORKABLE_JOB_API_URL = "https://apply.workable.com/api/v2/accounts/{account}/jobs/{shortcode}"
# The account's own name and website (the job API has neither): the
# company name for jobs whose source can't vouch for one (see
# app.services.company_names), and the logo domain.
_WORKABLE_ACCOUNT_API_URL = "https://apply.workable.com/api/v1/accounts/{account}"
_WORKABLE_JOB_URL_RE = re.compile(r"apply\.workable\.com/([^/?]+)/j/([a-zA-Z0-9]+)", re.IGNORECASE)
_WORKABLE_SHORTLINK_CODE_RE = re.compile(r"apply\.workable\.com/j/([a-zA-Z0-9]+)", re.IGNORECASE)
_WORKABLE_NOT_FOUND_RE = re.compile(r"^/oops\b|[?&]not_found=true", re.IGNORECASE)
_EMPLOYMENT_TYPES = {
    "full": EmploymentType.FULL_TIME,
    "part": EmploymentType.PART_TIME,
    "contract": EmploymentType.CONTRACT,
    "temporary": EmploymentType.TEMPORARY,
    "internship": EmploymentType.INTERNSHIP,
}
_WORKPLACE_TYPES = {
    "on_site": WorkplaceType.ONSITE,
    "hybrid": WorkplaceType.HYBRID,
    "remote": WorkplaceType.REMOTE,
}
# Requires the account-prefixed URL shape (apply.workable.com/{account}/j/...,
# e.g. as linked from a LinkedIn "Apply" redirect) — the bare shortlink
# form (apply.workable.com/j/{code}) resolves to the same job but doesn't
# carry the account slug this needs, so it just won't match.
_WORKABLE_ACCOUNT_JOB_RE = re.compile(r"apply\.workable\.com/([^/?]+)/j/", re.IGNORECASE)
# The bare board page (no specific job), e.g. as stored in
# CrawlSource.board_url — (?!j/) excludes the ambiguous shortlink shape
# above, which the pattern above already claims.
_WORKABLE_BOARD_RE = re.compile(r"apply\.workable\.com/(?!j/)([^/?]+)/?(?:\?|$)", re.IGNORECASE)
# Workable's short link form (apply.workable.com/j/{code}) carries no
# account slug, unlike the account-prefixed form above — but it
# 301-redirects to that same account-prefixed URL (verified live), so
# following the redirect and re-running the normal match against the
# resolved URL recovers the account slug.
_WORKABLE_SHORTLINK_RE = re.compile(r"apply\.workable\.com/j/[a-zA-Z0-9]+", re.IGNORECASE)


def _match(url: str) -> str | None:
    match = _WORKABLE_ACCOUNT_JOB_RE.search(url) or _WORKABLE_BOARD_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    # Free, public, unauthenticated API — no key required (the same feed
    # that powers Workable's embeddable "jobs widget").
    response = get_with_retry(_WORKABLE_JOBS_URL.format(board_key=board_key), timeout=TIMEOUT)
    response.raise_for_status()
    jobs = response.json().get("jobs", [])
    return limit_job_urls(job["url"] for job in jobs if job.get("url"))


def _detect_embedded(url: str) -> str | None:
    if not _WORKABLE_SHORTLINK_RE.search(url):
        return None
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return _match(str(response.url))


def _account_and_shortcode(url: str) -> tuple[str, str] | None:
    """(account slug, job shortcode) for a Workable job URL. The board feed
    lists jobs by their shortlink (apply.workable.com/j/{code}), which has
    no account slug; it 301-redirects to /{account}/j/{code} (verified
    live), so the redirect's Location header supplies it without loading
    the page. Raises httpx.HTTPError when resolving fails."""
    match = _WORKABLE_JOB_URL_RE.search(url)
    if match:
        return match.group(1), match.group(2)
    if not _WORKABLE_SHORTLINK_CODE_RE.search(url):
        return None
    response = httpx.get(url, timeout=TIMEOUT, follow_redirects=False)
    # The Location header is a relative path ("/{account}/j/{code}"). An
    # unknown or removed job redirects to "/oops" instead (verified live).
    location = response.headers.get("location")
    if response.status_code == 404 or (location and _WORKABLE_NOT_FOUND_RE.search(location)):
        raise httpx.HTTPStatusError(
            "Job not found", request=response.request, response=httpx.Response(404, request=response.request)
        )
    match = _WORKABLE_JOB_URL_RE.search(str(response.url.join(location))) if location else None
    return (match.group(1), match.group(2)) if match else None


@functools.lru_cache(maxsize=256)
def _account_info(account: str) -> tuple[str | None, str | None]:
    """(company name, company website) for a Workable account, cached per
    process: a scan lane drains one board's jobs, which share an account.
    Best-effort — a failure only costs the name, never the scan."""
    try:
        response = get_with_retry(_WORKABLE_ACCOUNT_API_URL.format(account=account), timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None, None
    return clean_text(data.get("name")), data.get("url") or None


def _location_of(job: dict) -> str | None:
    location = job.get("location") or {}
    parts = [clean_text(location.get(key)) for key in ("city", "region", "country")]
    return ", ".join(part for part in parts if part) or None


def _description_of(job: dict) -> str | None:
    sections = [job.get("description")]
    for heading, key in (("Requirements", "requirements"), ("Benefits", "benefits")):
        if job.get(key):
            sections.append(f"<h3>{heading}</h3>{job[key]}")
    return html_to_formatted_text("".join(section for section in sections if section))


def _workplace_of(job: dict) -> str:
    workplace = _WORKPLACE_TYPES.get(job.get("workplace") or "")
    if workplace:
        return workplace
    return WorkplaceType.REMOTE if job.get("remote") else WorkplaceType.UNKNOWN


def scan_job_url(url: str) -> ScanResult | None:
    if not (_WORKABLE_JOB_URL_RE.search(url) or _WORKABLE_SHORTLINK_CODE_RE.search(url)):
        return None
    try:
        ids = _account_and_shortcode(url)
        if ids is None:
            return ScanResult(success=False, error="Couldn't resolve the Workable account for this job URL.")
        response = get_with_retry(_WORKABLE_JOB_API_URL.format(account=ids[0], shortcode=ids[1]), timeout=TIMEOUT)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return ScanResult(success=False, error=str(exc), expired=exc.response.status_code == 404)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    job = response.json()
    if job.get("state") and job["state"] != "published":
        return ScanResult(success=False, error=f"Workable job is {job['state']}.", expired=True)

    description = _description_of(job)
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    company_name, company_url = _account_info(ids[0])
    return ScanResult(
        success=True,
        title=clean_text(job.get("title")),
        description=description,
        company_name=company_name,
        company_url=company_url,
        location=_location_of(job),
        workplace_type=_workplace_of(job),
        employment_type=_EMPLOYMENT_TYPES.get(job.get("type") or "", EmploymentType.UNKNOWN),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=base.posting_date(job.get("published")),
    )


ADAPTER = AtsAdapter(
    AtsType.WORKABLE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://apply.workable.com/{key}/",
    embedded_match=_detect_embedded,
    scan_job_url=scan_job_url,
)
