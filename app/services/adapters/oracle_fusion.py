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


# Some tenants front their Oracle Fusion candidate-experience site with a
# custom domain (a CNAME-style vanity URL, e.g. careers.claritev.com/en/
# sites/CX_1) that rewrites away the /hcmUI/CandidateExperience/ path
# segment _match needs — verified live against claritev.com, whose page
# still carries the real oraclecloud.com API host and site number in the
# same <base> tag Oracle's own SPA reads them from
# (data-apibaseurl="https://{host}:443" data-sitenumber="{site}"). Reused
# as both embedded_match (submission-time detection) and board_key
# (re-deriving from a *stored*, verbatim custom-domain board_url at crawl
# time) since both need the same one-page fetch.
#
# The captured host isn't always oraclecloud.com itself — some tenants
# (verified live: careersearch.stanford.edu) CNAME their whole stack,
# UI *and* REST API, onto their own domain, so data-apibaseurl
# self-references the vanity domain rather than pointing at a separate
# oraclecloud.com host. The REST API answers identically either way (same
# path, same response shape), so any host works, not just oraclecloud.com
# ones.
_EMBED_APIBASEURL_RE = re.compile(r'data-apibaseurl="https://([a-zA-Z0-9.-]+)(?::\d+)?"', re.IGNORECASE)
_EMBED_SITENUMBER_RE = re.compile(r'data-sitenumber="([^"]+)"', re.IGNORECASE)


def _detect_embedded(url: str) -> str | None:
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    host_match = _EMBED_APIBASEURL_RE.search(response.text)
    site_match = _EMBED_SITENUMBER_RE.search(response.text)
    if not host_match or not site_match:
        return None
    return f"{host_match.group(1)}/{site_match.group(1)}"


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

# Some tenants' job-posting links (as opposed to the board root _detect_embedded
# handles above) also live on a vanity domain instead of *.oraclecloud.com, so
# _JOB_URL_RE alone misses them even though the site number and job id are
# sitting right there in the path — verified live against American Express
# (careers.americanexpress.com/en/sites/CX_1/jobs/preview/{id}, one redirect
# hop from the canonical .../job/{id} page) and Texas Instruments
# (careers.ti.com/en/sites/CX/job/{id}, served directly, no redirect). Host-
# agnostic on purpose so it catches both the "job" and "jobs/preview" path
# shapes seen in the wild.
_JOB_PATH_RE = re.compile(r"/sites/([^/]+)/jobs?(?:/preview)?/(\d+)(?:/|$|\?)", re.IGNORECASE)


def _resolve(url: str) -> tuple[str, str, str] | None:
    """(host, site_number, job_id) needed for the requisition-details REST
    call. Direct oraclecloud.com job URLs carry all three already. A vanity-
    domain job URL doesn't carry the host, but does carry the site number
    and job id (_JOB_PATH_RE) — the host is recovered with the same one-page
    fetch/parse _detect_embedded above uses for board-level detection: follow
    redirects (Amex's preview link hops to its own canonical job page) and
    read the real API host out of the page's own <base> tag.
    """
    direct = _JOB_URL_RE.search(url)
    if direct:
        return direct.groups()
    path_match = _JOB_PATH_RE.search(url)
    if not path_match:
        return None
    site_number, job_id = path_match.groups()
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    host_match = _EMBED_APIBASEURL_RE.search(response.text)
    if not host_match:
        return None
    return host_match.group(1), site_number, job_id


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
    resolved = _resolve(url)
    if resolved is None:
        return None
    host, site_number, job_id = resolved
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
        # Verified live on American Express: this tenant's codes carry an
        # "ORA_" prefix (ORA_HYBRID, ORA_ON_SITE) the original mapping,
        # written before any tenant had WorkplaceTypeCode set at all,
        # didn't anticipate.
        return _WORKPLACE_TYPE_MAP.get(code.upper().removeprefix("ORA_"), WorkplaceType.UNKNOWN)
    return WorkplaceType.UNKNOWN


def _posted_at_of(job_data: dict[str, Any]) -> date | None:
    posted = job_data.get("ExternalPostedStartDate")
    if not isinstance(posted, str):
        return None
    try:
        return date.fromisoformat(posted[:10])
    except ValueError:
        return None


# Tenants configure their own extra requisition fields (Amex's include
# "Salary Range" and "Career Area", verified live) as freeform Prompt/Value
# pairs rather than fixed schema columns — Oracle's own UI renders them in
# the page's "Job Info" panel. Keyed by prompt so a duplicate prompt (seen
# on no tenant so far) just keeps the last value rather than erroring.
def _flex_fields_of(job_data: dict[str, Any]) -> dict[str, str]:
    entries = job_data.get("requisitionFlexFields")
    if not isinstance(entries, list):
        return {}
    fields: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        prompt, value = entry.get("Prompt"), entry.get("Value")
        if isinstance(prompt, str) and prompt.strip() and isinstance(value, str) and value.strip():
            fields[prompt.strip()] = value.strip()
    return fields


# No fixed field carries salary — when a tenant states one at all, it's
# prose inside one of these flex fields (e.g. "$65,500 - $81,000 annually
# + bonus + benefits"), so reuse the same prose parser job_scanner.py's
# generic fallback uses rather than writing a second one.
def _salary_of(flex_fields: dict[str, str]) -> tuple[int | None, int | None, str | None]:
    for prompt, value in flex_fields.items():
        if "salary" in prompt.lower() or "pay" in prompt.lower():
            salary_min, salary_max, currency = base.salary_from_text(value)
            if salary_min is not None:
                return salary_min, salary_max, currency
    return None, None, None


def _description_of(job_data: dict[str, Any], flex_fields: dict[str, str]) -> str | None:
    sections = [job_data.get("ExternalDescriptionStr")]
    if job_data.get("ExternalResponsibilitiesStr"):
        sections.append("<h3>Responsibilities</h3>" + job_data["ExternalResponsibilitiesStr"])
    if job_data.get("ExternalQualificationsStr"):
        sections.append("<h3>Qualifications</h3>" + job_data["ExternalQualificationsStr"])
    if job_data.get("CorporateDescriptionStr"):
        sections.append("<h3>About Us</h3>" + job_data["CorporateDescriptionStr"])

    # Category and the posting's application deadline are standard fields
    # on every tenant (not flex fields) but, like the flex fields above,
    # have nowhere else to go in ScanResult's fixed schema — Oracle's own
    # "Job Info" panel shows both alongside the tenant-configured ones.
    info: dict[str, str] = {}
    category = job_data.get("Category")
    if isinstance(category, str) and category.strip():
        info["Job Category"] = category.strip()
    apply_before = job_data.get("ExternalPostedEndDate")
    if isinstance(apply_before, str):
        try:
            info["Apply Before"] = date.fromisoformat(apply_before[:10]).isoformat()
        except ValueError:
            pass
    info.update(flex_fields)
    if info:
        items = "".join(f"<li><strong>{prompt}:</strong> {value}</li>" for prompt, value in info.items())
        sections.append(f"<h3>Additional Information</h3><ul>{items}</ul>")

    html = "".join(s for s in sections if isinstance(s, str) and s.strip())
    return html_to_formatted_text(html) if html else None


def extract(url: str, _html: str) -> ExtractedJobFields | None:
    job_data = _fetch_job_data(url)
    if job_data is None:
        return None
    title = job_data.get("Title")
    flex_fields = _flex_fields_of(job_data)
    salary_min, salary_max, salary_currency = _salary_of(flex_fields)
    return ExtractedJobFields(
        title=title if isinstance(title, str) else None,
        description=_description_of(job_data, flex_fields),
        location=_location_of(job_data),
        workplace_type=_workplace_type_of(job_data),
        employment_type=_employment_type_of(job_data),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=_posted_at_of(job_data),
        extracted_fields=flex_fields or None,
    )


def scan_job_url(url: str) -> ScanResult | None:
    if not (_JOB_URL_RE.search(url) or _JOB_PATH_RE.search(url)):
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
    if fields and fields.salary_min is not None:
        salary_min, salary_max, salary_currency = fields.salary_min, fields.salary_max, fields.salary_currency
    else:
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
        extracted_fields=fields.extracted_fields if fields else None,
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
    board_key=_detect_embedded,
    embedded_match=_detect_embedded,
    scan_job_url=scan_job_url,
)
