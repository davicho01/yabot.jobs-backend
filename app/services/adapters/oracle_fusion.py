import re
from datetime import date
from typing import Any

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import (
    DEFAULT_MAX_JOBS_PER_CRAWL,
    RECENT_WINDOW_DAYS,
    TIMEOUT,
    AtsAdapter,
    ExtractedJobFields,
    ScanResult,
    get_with_retry,
    is_recent_posting,
)
from app.services.adapters.text import MAX_LOCATION_LENGTH, html_to_formatted_text

_ORACLE_FUSION_JOBS_URL = "https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
_ORACLE_FUSION_JOB_URL = "https://{host}/hcmUI/CandidateExperience/en/sites/{site_number}/job/{job_id}"
_ORACLE_FUSION_PAGE_SIZE = 25
_ORACLE_FUSION_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
# Oracle Fusion's two-part board_key (tenant host, site number) both live in
# the path, so .search() rather than a full match recovers both straight out
# of a full job-posting URL (.../sites/{site}/job/{id}) as readily as a bare
# board URL.
_ORACLE_FUSION_URL_RE = re.compile(
    r"([a-zA-Z0-9.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[a-z]{2}/sites/([^/]+)", re.IGNORECASE
)


def _match(url: str) -> str | None:
    match = _ORACLE_FUSION_URL_RE.search(url)
    if not match:
        return None
    host, site_number = match.groups()
    return f"{host}/{site_number}"


def _fetch_jobs(board_key: str) -> list[str]:
    # Free, public, unauthenticated REST API — the same one Oracle's own
    # candidate-experience UI calls client-side, no key required.
    # PostedDate is a per-job field (verified sortBy=POSTING_DATES_DESC
    # returns newest first), so this uses the same RECENT_WINDOW_DAYS
    # early-exit as Workday/Amazon/Apple.
    host, _, site_number = board_key.partition("/")

    urls: list[str] = []
    offset = 0
    while len(urls) < _ORACLE_FUSION_MAX_JOBS:
        response = get_with_retry(
            _ORACLE_FUSION_JOBS_URL.format(host=host),
            params={
                "onlyData": "true",
                "expand": "requisitionList",
                "finder": (
                    f"findReqs;siteNumber={site_number},limit={_ORACLE_FUSION_PAGE_SIZE},"
                    f"offset={offset},sortBy=POSTING_DATES_DESC"
                ),
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        requisitions = items[0].get("requisitionList", []) if items else []
        if not requisitions:
            break

        recent = [r for r in requisitions if is_recent_posting(r.get("PostedDate"))]
        urls.extend(
            _ORACLE_FUSION_JOB_URL.format(host=host, site_number=site_number, job_id=r["Id"])
            for r in recent
            if r.get("Id")
        )
        if len(recent) < len(requisitions) or len(requisitions) < _ORACLE_FUSION_PAGE_SIZE:
            break
        offset += _ORACLE_FUSION_PAGE_SIZE

    return urls[:_ORACLE_FUSION_MAX_JOBS]


# Oracle Fusion's CandidateExperience job page is a client-rendered SPA:
# its static HTML carries no JSON-LD and nothing past title/description/
# company in og: tags — no location, schedule, or full description. But
# the same public recruitingCEJobRequisitionDetails REST API the SPA
# itself calls client-side is keyed by exactly the host/site-number/job-id
# already sitting in the URL, and returns the full requisition record —
# primary location, job schedule, and the complete description/
# responsibilities/qualifications HTML the og:description preview is
# truncated from.
_JOB_URL_RE = re.compile(
    r"([a-zA-Z0-9.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[a-z]{2}/sites/([^/]+)/job/(\d+)",
    re.IGNORECASE,
)
_DETAIL_URL = "https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"

# JobSchedule is a free-text label ("Full time"/"Part time"), not a fixed
# enum — only these two values have been observed on a real tenant, and
# it's often unset entirely, so anything else falls back to UNKNOWN like
# every other adapter's unmapped case.
_JOB_SCHEDULE_MAP = {
    "full time": EmploymentType.FULL_TIME,
    "part time": EmploymentType.PART_TIME,
}
# WorkplaceTypeCode is unset on every requisition seen on the one tenant
# this was verified against, but the field exists in Oracle's schema —
# mapped defensively so a tenant that does set it isn't left at UNKNOWN.
_WORKPLACE_TYPE_MAP = {
    "REMOTE": WorkplaceType.REMOTE,
    "HYBRID": WorkplaceType.HYBRID,
    "ON_SITE": WorkplaceType.ONSITE,
    "ONSITE": WorkplaceType.ONSITE,
}


def _fetch_job_data(url: str) -> dict[str, Any] | None:
    match = _JOB_URL_RE.search(url)
    if match is None:
        return None
    host, site_number, job_id = match.groups()
    try:
        response = httpx.get(
            _DETAIL_URL.format(host=host),
            params={
                "onlyData": "true",
                "expand": "all",
                "finder": f'ById;Id="{job_id}",siteNumber={site_number}',
            },
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    items = response.json().get("items", [])
    return items[0] if items and isinstance(items[0], dict) else None


def _location_of(job_data: dict[str, Any]) -> str | None:
    names = [job_data.get("PrimaryLocation")]
    for loc in job_data.get("secondaryLocations") or []:
        if isinstance(loc, dict):
            names.append(loc.get("Name"))
    location = ", ".join(dict.fromkeys(n for n in names if isinstance(n, str) and n.strip())) or None
    if location and len(location) > MAX_LOCATION_LENGTH:
        location = location[: MAX_LOCATION_LENGTH - 3] + "..."
    return location


def _employment_type_of(job_data: dict[str, Any]) -> str:
    schedule = job_data.get("JobSchedule")
    if isinstance(schedule, str):
        return _JOB_SCHEDULE_MAP.get(schedule.strip().lower(), EmploymentType.UNKNOWN)
    return EmploymentType.UNKNOWN


def _workplace_type_of(job_data: dict[str, Any]) -> str:
    code = job_data.get("WorkplaceTypeCode")
    if isinstance(code, str) and code:
        return _WORKPLACE_TYPE_MAP.get(code.upper(), WorkplaceType.UNKNOWN)
    return WorkplaceType.UNKNOWN


def _posted_at_of(job_data: dict[str, Any]) -> date | None:
    posted = job_data.get("ExternalPostedStartDate")
    if not isinstance(posted, str):
        return None
    try:
        return date.fromisoformat(posted[:10])
    except ValueError:
        return None


def _description_of(job_data: dict[str, Any]) -> str | None:
    sections = [job_data.get("ExternalDescriptionStr")]
    if job_data.get("ExternalResponsibilitiesStr"):
        sections.append("<h3>Responsibilities</h3>" + job_data["ExternalResponsibilitiesStr"])
    if job_data.get("ExternalQualificationsStr"):
        sections.append("<h3>Qualifications</h3>" + job_data["ExternalQualificationsStr"])
    if job_data.get("CorporateDescriptionStr"):
        sections.append("<h3>About Us</h3>" + job_data["CorporateDescriptionStr"])
    html = "".join(s for s in sections if isinstance(s, str) and s.strip())
    return html_to_formatted_text(html) if html else None


def extract(url: str, _html: str) -> ExtractedJobFields | None:
    job_data = _fetch_job_data(url)
    if job_data is None:
        return None
    title = job_data.get("Title")
    return ExtractedJobFields(
        title=title if isinstance(title, str) else None,
        description=_description_of(job_data),
        location=_location_of(job_data),
        workplace_type=_workplace_type_of(job_data),
        employment_type=_employment_type_of(job_data),
        posted_at=_posted_at_of(job_data),
    )


def scan_job_url(url: str) -> ScanResult | None:
    if not _JOB_URL_RE.search(url):
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
        workplace_type=fields.workplace_type if fields else WorkplaceType.UNKNOWN,
        employment_type=fields.employment_type if fields else EmploymentType.UNKNOWN,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=fields.posted_at if fields else None,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


# No to_board_url — board_url is stored verbatim, exactly as submitted,
# rather than reconstructed from board_key. See board_url_for_key in
# app.services.ats_adapters.
ADAPTER = AtsAdapter(
    AtsType.ORACLE_FUSION,
    match=_match,
    fetch_jobs=_fetch_jobs,
    scan_job_url=scan_job_url,
)
