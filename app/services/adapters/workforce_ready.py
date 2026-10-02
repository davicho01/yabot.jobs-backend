import re

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry, limit_job_urls
from app.services.adapters.text import html_to_formatted_text

# UKG Workforce Ready (formerly Kronos Workforce Ready) — a multi-tenant HR
# suite on secure{N}.saashr.com, careers at /ta/{companyId}.careers. The
# career page itself is an empty JS shell (verified live: Uintah Basin
# Technical College, secure7 / 6211828), but it's backed by a public,
# unauthenticated REST API that serves both the listing and each posting.
_URL_RE = re.compile(r"(secure\d*\.saashr\.com)/ta/(\d+)\.careers", re.IGNORECASE)
_JOB_ID_RE = re.compile(r"[?&]ShowJob=(\d+)", re.IGNORECASE)
_API_URL = "https://{host}/ta/rest/ui/recruitment/companies/%7C{company_id}/job-requisitions"
_JOB_URL = "https://{host}/ta/{company_id}.careers?ShowJob={job_id}"
_PAGE_SIZE = 100
# The detail endpoint answers a closed/unknown requisition with a 400 and
# this error code rather than a 404.
_INVALID_REQUISITION_CODE = 40197


def _match(url: str) -> str | None:
    match = _URL_RE.search(url)
    return f"{match.group(1).lower()}/{match.group(2)}" if match else None


def _split(board_key: str) -> tuple[str, str]:
    host, company_id = board_key.split("/", 1)
    return host, company_id


def _fetch_jobs(board_key: str) -> list[str]:
    host, company_id = _split(board_key)

    def job_ids():
        offset = 0
        while True:
            response = get_with_retry(
                _API_URL.format(host=host, company_id=company_id),
                params={"offset": offset, "size": _PAGE_SIZE, "sort": "desc", "lang": "en-US"},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            jobs = data.get("job_requisitions") or []
            yield from (job["id"] for job in jobs if isinstance(job, dict) and job.get("id"))
            offset += len(jobs)
            total = (data.get("_paging") or {}).get("total") or 0
            if not jobs or offset >= total:
                return

    return limit_job_urls(_JOB_URL.format(host=host, company_id=company_id, job_id=job_id) for job_id in job_ids())


def _location(location: dict | None) -> str | None:
    if not isinstance(location, dict):
        return None
    parts = [location.get(key) for key in ("city", "state", "country")]
    return ", ".join(part for part in parts if part) or None


def _employment_type(employee_type: dict | None) -> str:
    # Tenant-defined labels, e.g. "FT Exempt" / "PT Non-Exempt" (verified live).
    name = (employee_type or {}).get("name") or ""
    label = name.strip().lower()
    if label.startswith(("ft", "full")):
        return EmploymentType.FULL_TIME
    if label.startswith(("pt", "part")):
        return EmploymentType.PART_TIME
    if "temp" in label or "seasonal" in label:
        return EmploymentType.TEMPORARY
    if "intern" in label:
        return EmploymentType.INTERNSHIP
    return EmploymentType.UNKNOWN


def _as_int(value) -> int | None:
    try:
        return int(float(value)) if value else None
    except (TypeError, ValueError):
        return None


def scan_job_url(url: str) -> ScanResult | None:
    board_key = _match(url)
    job_id_match = _JOB_ID_RE.search(url)
    if board_key is None or job_id_match is None:
        return None
    host, company_id = _split(board_key)
    try:
        response = get_with_retry(
            f"{_API_URL.format(host=host, company_id=company_id)}/{job_id_match.group(1)}",
            params={"lang": "en-US"},
            timeout=TIMEOUT,
        )
        if response.status_code == 400:
            codes = [error.get("code") for error in response.json().get("errors") or [] if isinstance(error, dict)]
            if _INVALID_REQUISITION_CODE in codes:
                return ScanResult(success=False, error="Job requisition is no longer valid", expired=True)
        response.raise_for_status()
        job = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return ScanResult(success=False, error=str(exc))

    sections = [job.get("job_description"), job.get("job_requirement")]
    description = "\n\n".join(html_to_formatted_text(html) for html in sections if html)
    salary_min, salary_max = _as_int(job.get("base_pay_from")), _as_int(job.get("base_pay_to"))
    return ScanResult(
        success=True,
        title=job.get("job_title"),
        description=description or None,
        location=_location(job.get("location")),
        workplace_type=WorkplaceType.REMOTE if job.get("is_remote_job") else WorkplaceType.UNKNOWN,
        employment_type=_employment_type(job.get("employee_type")),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency="USD" if salary_min or salary_max else None,
        raw_html_excerpt=response.text[:20_000],
        full_html=response.text,
    )


ADAPTER = AtsAdapter(
    AtsType.WORKFORCE_READY,
    match=_match,
    board_key=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: "https://{0}/ta/{1}.careers?CareersSearch".format(*_split(key)),
    scan_job_url=scan_job_url,
)
