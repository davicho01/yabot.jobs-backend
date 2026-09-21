import re
from urllib.parse import parse_qs, urlsplit

import httpx

from app.models.enums import AtsType
from app.services.adapters import base
from app.services.adapters.base import (
    DEFAULT_MAX_JOBS_PER_CRAWL,
    TIMEOUT,
    AtsAdapter,
    ExtractedJobFields,
    ScanResult,
    get_with_retry,
    limit_job_urls,
    post_with_retry,
)
from app.services.adapters.text import clean_text, extract_balanced_div, html_to_formatted_text
from app.services.browser_fetch import fetch_rendered_html

# Oracle Taleo — a multi-tenant ATS at {tenant}.taleo.net. One tenant hosts
# several independent "career sections" (e.g. Textron's "textron" for the
# corporate/general site, "bell" for Bell, "tabbu" for Textron Aviation —
# verified live), each its own board with its own job list, same shape as
# Workday's "company/instance/site" or CSOD's "corp/site".
_TALEO_HOST_RE = re.compile(r"([a-z0-9-]+)\.taleo\.net", re.IGNORECASE)
_SECTION_RE = re.compile(r"/careersection/([^/]+)/", re.IGNORECASE)
_TALEO_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_SEARCH_URL = "https://{tenant}.taleo.net/careersection/rest/jobboard/searchjobs"
_JOBSEARCH_URL = "https://{tenant}.taleo.net/careersection/{section}/jobsearch.ftl?lang=en"
_JOB_URL = "https://{tenant}.taleo.net/careersection/{section}/jobdetail.ftl?job={job_id}&lang=en"
# The public search API 400s without these — a plain client-timezone pair,
# not a secret/session value (verified: works with no cookies at all, fresh
# process, any tz string).
_TZ_HEADERS = {"tz": "America/New_York", "tzname": "EST"}
_PORTAL_NO_RE = re.compile(r"portalNo\s*:\s*'(\d+)'")
_EMPTY_SEARCH_FILTERS = [
    {"id": fid, "selectedValues": []}
    for fid in ("POSTING_DATE", "LOCATION", "JOB_FIELD", "JOB_TYPE", "JOB_SCHEDULE", "JOB_LEVEL")
]
_EMPTY_ADVANCED_FILTERS = [
    {"id": fid, "selectedValues": []}
    for fid in (
        "ORGANIZATION",
        "LOCATION",
        "JOB_FIELD",
        "JOB_NUMBER",
        "URGENT_JOB",
        "EMPLOYEE_STATUS",
        "WILL_TRAVEL",
        "JOB_SHIFT",
    )
]


def _match(url: str) -> str | None:
    host_match = _TALEO_HOST_RE.search(url)
    section_match = _SECTION_RE.search(url)
    if not host_match or not section_match:
        return None
    return f"{host_match.group(1)}/{section_match.group(1)}"


def _board_url(board_key: str) -> str:
    tenant, section = board_key.split("/")
    return _JOBSEARCH_URL.format(tenant=tenant, section=section)


def _portal_no(tenant: str, section: str) -> str | None:
    response = get_with_retry(_JOBSEARCH_URL.format(tenant=tenant, section=section), timeout=TIMEOUT)
    response.raise_for_status()
    match = _PORTAL_NO_RE.search(response.text)
    return match.group(1) if match else None


def _search(tenant: str, portal_no: str, *, page_no: int, job_number: str | None = None) -> dict:
    advanced_filters = [dict(f) for f in _EMPTY_ADVANCED_FILTERS]
    if job_number is not None:
        for f in advanced_filters:
            if f["id"] == "JOB_NUMBER":
                f["selectedValues"] = [job_number]
    body = {
        "multilineEnabled": False,
        "sortingSelection": {"sortBySelectionParam": "3", "ascendingSortingOrder": "false"},
        "fieldData": {"fields": {"KEYWORD": "", "LOCATION": ""}, "valid": True},
        "filterSelectionParam": {"searchFilterSelections": _EMPTY_SEARCH_FILTERS},
        "advancedSearchFiltersSelectionParam": {"searchFilterSelections": advanced_filters},
        "pageNo": page_no,
    }
    response = post_with_retry(
        _SEARCH_URL.format(tenant=tenant),
        params={"lang": "en", "portal": portal_no},
        headers={**_TZ_HEADERS, "Content-Type": "application/json"},
        json=body,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _fetch_jobs(board_key: str) -> list[str]:
    tenant, section = board_key.split("/")
    portal_no = _portal_no(tenant, section)
    if portal_no is None:
        raise ValueError(f"Couldn't resolve a Taleo portal number for tenant={tenant!r} section={section!r}")

    job_ids: list[str] = []
    page_no = 1
    total = None
    while len(job_ids) < _TALEO_MAX_JOBS and (total is None or len(job_ids) < total):
        data = _search(tenant, portal_no, page_no=page_no)
        total = data.get("pagingData", {}).get("totalCount", 0)
        batch = data.get("requisitionList", [])
        if not batch:
            break
        for req in batch:
            job_id = req.get("contestNo")
            if job_id:
                job_ids.append(job_id)
        page_no += 1
    return limit_job_urls(_JOB_URL.format(tenant=tenant, section=section, job_id=job_id) for job_id in job_ids)


# The metadata sidebar (Recruiting Company, Primary Location, Schedule,
# Worksite, ...) and the free-form description body are both populated
# client-side from a serialized initial-state blob that's technically
# present in the raw HTML but not in a form worth hand-parsing (an
# internal "!|!"-delimited pseudo-CSV whose field order/escaping isn't
# stable) — genuinely nothing usable without JS having run, hence the
# browser-render fallback (verified live: every value below sits in a
# `<span class="text">` right after its `<span class="subtitle">` label
# once rendered, both empty in the raw response).
_DESCRIPTION_MARKER = 'class="editablesection"'
# The <title> tag reliably renders as "Job Description - {title} ({job
# number})" (verified live) — strip that wrapper rather than depend on the
# metadata sidebar's own DOM shape, which turned out not to match this
# tenant's actual rendered markup (see _META_VALUE_RE below).
_TITLE_TAG_RE = re.compile(r"^Job Description - (.+?)\s*\(\d+\)\s*$")


def _title_of(html: str) -> str | None:
    raw = base.fallback_title(html)
    if not raw:
        return None
    match = _TITLE_TAG_RE.match(raw.strip())
    return clean_text(match.group(1)) if match else clean_text(raw)


# The metadata sidebar (Recruiting Company, Primary Location, Schedule,
# Worksite, ...) turned out — verified against real prod scans — not to
# render into the `<span class="subtitle">`/`<span class="text">` pairing
# the raw (pre-render) template suggested; whatever ends up filling those
# spans doesn't match after a real render. But every value there also
# appears as plain "Label: Value" text inside the description body itself
# (both markdown-heading style, "## Primary Location\n\n: value", and
# plain-line style, "Worksite: value" — verified live, format isn't even
# consistent within one page), which html_to_formatted_text already
# reduces to reliably, so pull them from there instead.
def _meta_value(description: str, label: str) -> str | None:
    match = re.search(rf"{re.escape(label)}\s*\n*:\s*([^\n]+)", description)
    return clean_text(match.group(1)) if match else None


def extract(html: str) -> ExtractedJobFields:
    description = None
    marker = html.find(_DESCRIPTION_MARKER)
    if marker != -1:
        div_start = html.rfind("<div", 0, marker)
        if div_start != -1:
            inner = extract_balanced_div(html, div_start)
            description = html_to_formatted_text(inner) if inner else None

    return ExtractedJobFields(
        title=_title_of(html),
        description=description,
        company_name=_meta_value(description, "Recruiting Company") if description else None,
        location=_meta_value(description, "Primary Location") if description else None,
    )


def scan_job_url(url: str) -> ScanResult | None:
    if _match(url) is None:
        return None
    job_id_values = parse_qs(urlsplit(url).query).get("job")
    if not job_id_values:
        return None

    html = fetch_rendered_html(url)
    if html is None:
        return ScanResult(success=False, error="Taleo job detail requires JS rendering; browser fetch unavailable.")

    fields = extract(html)
    description = fields.description or base.fallback_description(html) or base.og_description(html)
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=fields.title or base.fallback_title(html),
        description=description,
        company_name=fields.company_name,
        location=fields.location,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.TALEO,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=_board_url,
    scan_job_url=scan_job_url,
)
