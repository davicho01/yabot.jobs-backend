import json
import re
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, ScanResult, get_with_retry
from app.services.adapters.text import clean_text, html_to_formatted_text

_GUPY_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_GUPY_URL_RE = re.compile(r"([a-zA-Z0-9-]+\.gupy\.io)", re.IGNORECASE)
# Gupy is a Next.js app: the board root server-renders the full job list
# straight into __NEXT_DATA__ (props.pageProps.jobs, every open role at
# once — verified live: Assaí Atacadista returned all 3347 in one fetch, no
# pagination needed) rather than exposing it as a separate API endpoint.
# Each job object carries only a numeric id, no slug/URL — but
# {host}/jobs/{id} 200s and serves the real posting (verified live), so
# that's built directly instead of scraping a link out of the page.
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)
_JOB_URL = "https://{host}/jobs/{job_id}"


def _match(url: str) -> str | None:
    match = _GUPY_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(host: str) -> list[str]:
    response = get_with_retry(f"https://{host}", timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    match = _NEXT_DATA_RE.search(response.text)
    if match is None:
        return []
    data = json.loads(match.group(1))
    jobs = data.get("props", {}).get("pageProps", {}).get("jobs") or []
    urls = [_JOB_URL.format(host=host, job_id=job["id"]) for job in jobs if job.get("id")]
    return urls[:_GUPY_MAX_JOBS]


def _board_key(url: str) -> str | None:
    return urlsplit(url).netloc or None


# A job page ({host}/jobs/{id}) is the same Next.js app: no JSON-LD, and its
# <title>/meta are a generic "Página da Vaga" shell (all a generic scan ever
# got — verified 2026-10-12), but __NEXT_DATA__ carries the whole posting
# under props.pageProps.job.
_GUPY_JOB_URL_RE = re.compile(r"[a-zA-Z0-9-]+\.gupy\.io/jobs/\d+", re.IGNORECASE)
_DESCRIPTION_SECTIONS = (
    ("description", None),
    ("responsibilities", "Responsabilidades"),
    ("prerequisites", "Requisitos"),
    ("additionalInformation", "Informações adicionais"),
)
# jobType values seen on live postings; anything else stays unknown.
_EMPLOYMENT_TYPES = {
    "vacancy_type_effective": EmploymentType.FULL_TIME,
    "vacancy_type_internship": EmploymentType.INTERNSHIP,
    "vacancy_type_temporary": EmploymentType.TEMPORARY,
    "vacancy_type_freelancer": EmploymentType.CONTRACT,
}
_WORKPLACE_TYPES = {
    "on-site": WorkplaceType.ONSITE,
    "onsite": WorkplaceType.ONSITE,
    "hybrid": WorkplaceType.HYBRID,
    "remote": WorkplaceType.REMOTE,
}


def _job_of(html: str) -> dict | None:
    match = _NEXT_DATA_RE.search(html)
    if match is None:
        return None
    try:
        job = json.loads(match.group(1)).get("props", {}).get("pageProps", {}).get("job")
    except json.JSONDecodeError:
        return None
    return job if isinstance(job, dict) and job.get("name") else None


def _description_of(job: dict) -> str | None:
    parts = []
    for key, heading in _DESCRIPTION_SECTIONS:
        if isinstance(job.get(key), str) and job[key].strip():
            parts.append((f"<h3>{heading}</h3>" if heading else "") + job[key])
    return html_to_formatted_text("".join(parts))


def _location_of(job: dict) -> str | None:
    parts = [clean_text(job.get(key)) for key in ("addressCity", "addressState", "addressCountry")]
    return ", ".join(part for part in parts if part) or None


def scan_job_url(url: str) -> ScanResult | None:
    if not _GUPY_JOB_URL_RE.search(url):
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    job = _job_of(page.text)
    if job is None:
        return ScanResult(success=False, error="Gupy job page carried no job data.")
    if job.get("status") and job["status"] != "published":
        return ScanResult(success=False, error=f"Gupy job is {job['status']}.", expired=True)

    description = _description_of(job)
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    career_page = job.get("careerPage") if isinstance(job.get("careerPage"), dict) else {}
    return ScanResult(
        success=True,
        title=clean_text(job.get("name")),
        description=description,
        company_name=clean_text(career_page.get("name")),
        location=_location_of(job),
        workplace_type=_WORKPLACE_TYPES.get(job.get("workplaceType") or "", WorkplaceType.UNKNOWN),
        employment_type=_EMPLOYMENT_TYPES.get(job.get("jobType") or "", EmploymentType.UNKNOWN),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=base.posting_date(job.get("publishedAt")),
    )


# No to_board_url — {tenant}.gupy.io is already the canonical shared host,
# same reasoning as Avature/iCIMS; board_url is stored verbatim.
ADAPTER = AtsAdapter(
    AtsType.GUPY,
    match=_match,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
    scan_job_url=scan_job_url,
)
