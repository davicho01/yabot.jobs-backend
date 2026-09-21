import re
from datetime import date
from typing import Any
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
    posting_date,
)
from app.services.adapters.text import html_to_formatted_text

_ADP_JOBS_URL = "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"
_ADP_JOB_DETAIL_URL = _ADP_JOBS_URL + "/{job_id}"
_ADP_JOB_URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
    "?cid={cid}{cc_id_param}&type=JS&lang=en_US&selectedMenuKey=CareerCenter&jobId={job_id}"
)
_ADP_PAGE_SIZE = 50
# ADP client career sites (one company's own postings) run nowhere near
# Amazon/Google scale — no "today only" early-exit needed, just a safety cap.
_ADP_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_ADP_URL_RE = re.compile(r"workforcenow\.adp\.com", re.IGNORECASE)


def _match(url: str) -> str | None:
    # ADP's client id (cid) is the only identifier every client careers URL
    # has — visible in the query string, not a single path segment (and
    # query param order isn't guaranteed, so this parses the query string
    # properly rather than trying a positional regex). The career-center id
    # (ccId) disambiguates clients with more than one career center, but
    # most clients have just one and their URLs simply omit it — the
    # job-requisitions API happily returns that one default career center's
    # postings with cid alone, so ccId is optional here too.
    if not _ADP_URL_RE.search(url):
        return None
    query = parse_qs(urlsplit(url).query)
    cid = query.get("cid", [None])[0]
    cc_id = query.get("ccId", [None])[0]
    if not cid:
        return None
    return f"{cid}/{cc_id or ''}"


def _board_url(board_key: str) -> str:
    cid, cc_id = board_key.split("/")
    cc_id_param = f"&ccId={cc_id}" if cc_id else ""
    return f"https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={cid}{cc_id_param}"


def _external_job_id(req: dict[str, Any]) -> str | None:
    return next(
        (
            field["stringValue"]
            for field in req.get("customFieldGroup", {}).get("stringFields", [])
            if field.get("nameCode", {}).get("codeValue") == "ExternalJobID"
        ),
        None,
    )


def _search_requisitions(cid: str, cc_id: str | None) -> list[dict[str, Any]]:
    # Free, public, unauthenticated API — no key required (the same feed
    # ADP's own JS-rendered career-center page calls).
    cc_id_param = {"ccId": cc_id} if cc_id else {}
    requisitions: list[dict[str, Any]] = []
    skip = 0
    while len(requisitions) < _ADP_MAX_JOBS:
        response = get_with_retry(
            _ADP_JOBS_URL,
            params={
                "cid": cid,
                **cc_id_param,
                "lang": "en_US",
                "locale": "en_US",
                "$top": _ADP_PAGE_SIZE,
                "$skip": skip,
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        batch = response.json().get("jobRequisitions", [])
        if not batch:
            break
        requisitions.extend(batch)
        if len(batch) < _ADP_PAGE_SIZE:
            break
        skip += _ADP_PAGE_SIZE
    return requisitions[:_ADP_MAX_JOBS]


def _fetch_jobs(board_key: str) -> list[str]:
    # Each requisition buries its externally-visible job id inside a
    # generic key/value bag (customFieldGroup.stringFields) rather than a
    # top-level field.
    cid, cc_id = board_key.split("/")
    cc_id_url_param = f"&ccId={cc_id}" if cc_id else ""
    urls: list[str] = []
    for req in _search_requisitions(cid, cc_id):
        job_id = _external_job_id(req)
        if job_id:
            urls.append(_ADP_JOB_URL.format(cid=cid, cc_id_param=cc_id_url_param, job_id=job_id))
    return urls[:_ADP_MAX_JOBS]


def _location_of(req: dict[str, Any]) -> str | None:
    texts = list(
        dict.fromkeys(
            loc["nameCode"]["shortName"].strip()
            for loc in req.get("requisitionLocations", [])
            if isinstance(loc.get("nameCode"), dict) and loc["nameCode"].get("shortName")
        )
    )
    return "; ".join(texts) or None


def _posted_at_of(req: dict[str, Any]) -> date | None:
    return posting_date(req.get("postDate"))


def _fields_of(req: dict[str, Any]) -> ExtractedJobFields:
    return ExtractedJobFields(
        title=req.get("requisitionTitle"),
        description=html_to_formatted_text(req.get("requisitionDescription")),
        location=_location_of(req),
        posted_at=_posted_at_of(req),
    )


def extract(cid: str, cc_id: str | None, job_id: str) -> ExtractedJobFields | None:
    # A dedicated per-requisition endpoint, keyed by the same external job
    # id the URL's jobId= param already carries — no need to page through
    # the whole listing to find one job. Crucially, unlike the listing
    # endpoint, this one *does* carry the full description (as HTML) —
    # the job detail page itself is a JS SPA shell with no server-rendered
    # content and no JSON-LD/OG description either (verified live: raw
    # <title> is a static "Recruitment" placeholder), and even a real
    # headless-browser render doesn't help here: the description renders
    # inside open shadow DOM (ADP's own "sdf-*" web-component design
    # system), which a plain document.outerHTML capture never serializes
    # (verified live — the rendered page has real layout and 12k+
    # characters of shadow-rendered text, but zero characters of visible
    # light-DOM/innerText). This REST endpoint sidesteps all of that.
    cc_id_param = {"ccId": cc_id} if cc_id else {}
    try:
        response = get_with_retry(
            _ADP_JOB_DETAIL_URL.format(job_id=job_id),
            params={"cid": cid, **cc_id_param, "lang": "en_US", "locale": "en_US"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    req = response.json()
    if not isinstance(req, dict) or not req.get("requisitionTitle"):
        return None
    return _fields_of(req)


def scan_job_url(url: str) -> ScanResult | None:
    board_key = _match(url)
    if board_key is None:
        return None
    cid, cc_id = board_key.split("/")
    job_id = parse_qs(urlsplit(url).query).get("jobId", [None])[0]
    if not job_id:
        return None

    fields = extract(cid, cc_id or None, job_id)
    if fields is None:
        return ScanResult(success=False, error="Requisition not found (removed, filled, or an invalid job id).")

    salary_min, salary_max, salary_currency = base.salary_from_text(fields.description)
    return ScanResult(
        success=True,
        title=fields.title,
        description=fields.description,
        location=fields.location,
        posted_at=fields.posted_at,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
    )


ADAPTER = AtsAdapter(
    AtsType.ADP,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=_board_url,
    scan_job_url=scan_job_url,
)
