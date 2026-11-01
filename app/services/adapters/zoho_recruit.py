import html as html_lib
import json
import re

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import (
    TIMEOUT,
    AtsAdapter,
    BoardListing,
    ScanResult,
    get_with_retry,
    limit_job_urls,
    posting_date,
)
from app.services.adapters.text import clean_text, html_to_formatted_text

# {tenant}.zohorecruit.com, or the same tenant on another Zoho data centre.
# www. is Zoho's own marketing site, not a board.
_HOST = r"(?!www\.)[a-z0-9-]+\.zohorecruit\.(?:com\.au|com\.cn|com|eu|in|jp|ca|sa|cn)"
_HOST_RE = re.compile(rf"(?:^|//)({_HOST})(?=[/:?#]|$)", re.IGNORECASE)
_BOARD_URL = "https://{host}/jobs/Careers"
# The board page is a JS shell, but it inlines every job record as an HTML-
# escaped JSON array in <input id="jobs" value="...">. Unpublished and
# locked records stay in the array (Publish false, Is_Locked true) after
# they come down, so only Publish says whether a job is open.
_JOBS_INPUT_RE = re.compile(r"<input[^>]*\bid=\"jobs\"[^>]*>", re.IGNORECASE)
_VALUE_RE = re.compile(r"\bvalue=\"([^\"]*)\"")
# A job's own page inlines its record as a JS string literal instead:
# var jobs = JSON.parse('[{\x22Posting_Title\x22:...}]').
_JOB_LITERAL_RE = re.compile(r"var jobs = JSON\.parse\('((?:[^'\\]|\\.)*)'\)")
_JS_ESCAPE_RE = re.compile(r"\\(x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)")
_JS_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r"}
# /jobs/Careers/{id}/{slug}: the slug is cosmetic and goes stale when a job is
# retitled (verified 2026-11-01: .../785637000005641021/x serves the same
# page), so jobs are listed by id alone.
_JOB_URL = "https://{host}/jobs/Careers/{job_id}"
_JOB_PAGE_RE = re.compile(rf"//({_HOST})/jobs/Careers/(\d+)", re.IGNORECASE)
_SITE_NAME_RE = re.compile(r"<meta[^>]+property=\"og:site_name\"[^>]+content=\"([^\"]*)\"", re.IGNORECASE)
_EMPLOYMENT_TYPES = {
    "full time": EmploymentType.FULL_TIME,
    "part time": EmploymentType.PART_TIME,
    "contract": EmploymentType.CONTRACT,
    "freelance": EmploymentType.CONTRACT,
    "temporary": EmploymentType.TEMPORARY,
    "internship": EmploymentType.INTERNSHIP,
}


def _match(url: str) -> str | None:
    match = _HOST_RE.search(url)
    return match.group(1).lower() if match else None


def _is_open(job: dict) -> bool:
    return bool(job.get("Publish")) and not job.get("Is_Locked")


def _board_jobs(page_html: str) -> list[dict]:
    tag = _JOBS_INPUT_RE.search(page_html)
    value = _VALUE_RE.search(tag.group(0)) if tag else None
    if value is None:
        raise ValueError("Zoho Recruit board has no inlined job list.")
    return json.loads(html_lib.unescape(value.group(1)))


def _fetch_jobs(host: str) -> BoardListing:
    response = get_with_retry(_BOARD_URL.format(host=host), timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    open_jobs = [job for job in _board_jobs(response.text) if job.get("id") and _is_open(job)]
    urls = limit_job_urls(_JOB_URL.format(host=host, job_id=job["id"]) for job in open_jobs)
    posted = {}
    for job in open_jobs:
        url = _JOB_URL.format(host=host, job_id=job["id"])
        if url in urls and (day := posting_date(job.get("Date_Opened"))) is not None:
            posted[url] = day
    return BoardListing(urls, complete=len(urls) == len(open_jobs), posted=posted)


def _js_unescape(literal: str) -> str:
    def replace(match: re.Match) -> str:
        escape = match.group(1)
        if len(escape) > 1:
            return chr(int(escape[1:], 16))
        return _JS_SIMPLE_ESCAPES.get(escape, escape)

    return _JS_ESCAPE_RE.sub(replace, literal)


def _page_job(page_html: str, job_id: str) -> dict | None:
    literal = _JOB_LITERAL_RE.search(page_html)
    if literal is None:
        return None
    try:
        jobs = json.loads(_js_unescape(literal.group(1)))
    except ValueError:
        return None
    return next((job for job in jobs if str(job.get("id")) == job_id), None)


def _location_of(job: dict) -> str | None:
    parts = []
    for key in ("City", "State", "Country"):
        part = clean_text(job.get(key))
        if part and part not in parts:
            parts.append(part)
    return ", ".join(parts) or None


def scan_job_url(url: str) -> ScanResult | None:
    page_match = _JOB_PAGE_RE.search(url)
    if page_match is None:
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    job = _page_job(page.text, page_match.group(2))
    if job is None:
        return ScanResult(success=False, error="Zoho Recruit page has no job record.")
    if not _is_open(job):
        return ScanResult(success=False, error="Zoho Recruit no longer publishes this job.", expired=True)

    description = html_to_formatted_text(job.get("Job_Description") or "") or base.fallback_description(page.text)
    salary_min, salary_max, salary_currency = base.salary_from_text(job.get("Salary") or description)
    site_name = _SITE_NAME_RE.search(page.text)
    location = _location_of(job)
    remote = "remote" in f"{job.get('City') or ''} {job.get('Posting_Title') or ''}".lower()
    return ScanResult(
        success=True,
        title=clean_text(job.get("Posting_Title")) or base.fallback_title(page.text),
        description=description,
        company_name=clean_text(html_lib.unescape(site_name.group(1))) if site_name else None,
        location=location,
        workplace_type=WorkplaceType.REMOTE if remote else WorkplaceType.UNKNOWN,
        employment_type=_EMPLOYMENT_TYPES.get((job.get("Job_Type") or "").lower(), EmploymentType.UNKNOWN),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=posting_date(job.get("Date_Opened")),
        raw_html_excerpt=page.text[:20_000],
        full_html=page.text,
    )


ADAPTER = AtsAdapter(
    AtsType.ZOHO_RECRUIT,
    match=_match,
    fetch_jobs=_fetch_jobs,
    scan_job_url=scan_job_url,
)
