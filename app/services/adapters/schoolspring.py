import re

import httpx

from app.models.enums import AtsType, EmploymentType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry, limit_job_urls
from app.services.adapters.text import html_to_formatted_text

# SchoolSpring (PowerSchool) — a multi-tenant K-12 job board at
# {district}.schoolspring.com. The site is a React SPA over a public,
# unauthenticated REST API on api.schoolspring.com, scoped per board by a
# domainName parameter (verified live: slcschools.schoolspring.com, 53 jobs).
# robots.txt allows everything. www. is the national aggregator spanning
# every district, and api. is the API host — neither is a single board.
_HOST_RE = re.compile(r"\b((?!www\.|api\.)[a-z0-9-]+\.schoolspring\.com)", re.IGNORECASE)
_JOB_ID_RE = re.compile(r"[?&]jobId=(\d+)", re.IGNORECASE)
_API = "https://api.schoolspring.com/api/Jobs"
_JOB_URL = "https://{host}/jobdetail?jobId={job_id}"
_PAGE_SIZE = 100
# Every filter must be present (empty) or the search endpoint rejects the
# request — mirrors the SPA's own query string.
_EMPTY_FILTERS = dict.fromkeys(
    ("keyword", "location", "category", "gradelevel", "jobtype", "organization", "swLat", "swLon", "neLat", "neLon"), ""
)


def _match(url: str) -> str | None:
    match = _HOST_RE.search(url)
    return match.group(1).lower() if match else None


def _fetch_jobs(host: str) -> list[str]:
    def job_ids():
        page = 1
        while True:
            response = get_with_retry(
                f"{_API}/GetPagedJobsWithSearch",
                params={"domainName": host, **_EMPTY_FILTERS, "page": page, "size": _PAGE_SIZE, "sortDateAscending": "false"},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            jobs = ((response.json().get("value") or {}).get("jobsList")) or []
            yield from (job["jobId"] for job in jobs if isinstance(job, dict) and job.get("jobId"))
            if len(jobs) < _PAGE_SIZE:
                return
            page += 1

    return limit_job_urls(_JOB_URL.format(host=host, job_id=job_id) for job_id in job_ids())


def _employment_type(job_type: str | None) -> str:
    label = (job_type or "").lower()
    if "full" in label:
        return EmploymentType.FULL_TIME
    if "part" in label:
        return EmploymentType.PART_TIME
    if "temp" in label or "substitute" in label or "seasonal" in label:
        return EmploymentType.TEMPORARY
    if "intern" in label:
        return EmploymentType.INTERNSHIP
    return EmploymentType.UNKNOWN


def _location(job: dict, locations: list) -> str | None:
    places = [loc.get("displayLocation") for loc in locations if isinstance(loc, dict) and loc.get("displayLocation")]
    if places:
        return "; ".join(dict.fromkeys(places))
    city_state = [job.get("contactCity"), job.get("stateName")]
    return ", ".join(part for part in city_state if part) or None


def _pay(value) -> int | None:
    try:
        return int(float(value)) or None
    except (TypeError, ValueError):
        return None


def scan_job_url(url: str) -> ScanResult | None:
    host = _match(url)
    job_id_match = _JOB_ID_RE.search(url)
    if host is None or job_id_match is None:
        return None
    try:
        response = get_with_retry(f"{_API}/{job_id_match.group(1)}", params={"domainName": host}, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return ScanResult(success=False, error=str(exc))

    value = data.get("value") or {}
    job = value.get("jobInfo")
    if not data.get("success") or not isinstance(job, dict):
        # Closed/unknown postings answer 200 with success=false and
        # "JobDetail not found" (verified live), not a 404.
        return ScanResult(success=False, error=data.get("message") or "SchoolSpring job not found", expired=True)

    sections = [job.get("jobDescription"), job.get("requirements")]
    description = "\n\n".join(text for html in sections if html and (text := html_to_formatted_text(html)))
    salary_min, salary_max = (_pay(job.get("payMin")), _pay(job.get("payMax"))) if job.get("payDisplay") else (None, None)
    return ScanResult(
        success=True,
        title=job.get("jobTitle"),
        description=description or None,
        company_name=job.get("employerName"),
        location=_location(job, value.get("jobLocations") or []),
        employment_type=_employment_type(job.get("jobTypeName")),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency="USD" if salary_min or salary_max else None,
        posted_at=base.posting_date(job.get("displayDate")),
        raw_html_excerpt=response.text[:20_000],
        full_html=response.text,
    )


ADAPTER = AtsAdapter(
    AtsType.SCHOOLSPRING,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda host: f"https://{host}/",
    scan_job_url=scan_job_url,
)
