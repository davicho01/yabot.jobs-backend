import re
from urllib.parse import parse_qs, urlsplit

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter

_ADP_JOBS_URL = "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"
_ADP_JOB_URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
    "?cid={cid}&ccId={cc_id}&type=JS&lang=en_US&selectedMenuKey=CareerCenter&jobId={job_id}"
)
_ADP_PAGE_SIZE = 50
# ADP client career sites (one company's own postings) run nowhere near
# Amazon/Google scale — no "today only" early-exit needed, just a safety cap.
_ADP_MAX_JOBS = 500
_ADP_URL_RE = re.compile(r"workforcenow\.adp\.com", re.IGNORECASE)


def _match(url: str) -> str | None:
    # Unlike the other platforms, ADP needs two identifiers, not one — the
    # client id (cid) and the career-center id (ccId), both only visible in
    # a client's careers URL query string, not a single path segment (and
    # query param order isn't guaranteed, so this parses the query string
    # properly rather than trying a positional regex).
    if not _ADP_URL_RE.search(url):
        return None
    query = parse_qs(urlsplit(url).query)
    cid = query.get("cid", [None])[0]
    cc_id = query.get("ccId", [None])[0]
    if not (cid and cc_id):
        return None
    return f"{cid}/{cc_id}"


def _board_url(board_key: str) -> str:
    cid, cc_id = board_key.split("/")
    return f"https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={cid}&ccId={cc_id}"


def _fetch_jobs(board_key: str) -> list[str]:
    # Free, public, unauthenticated API — no key required (the same feed
    # ADP's own JS-rendered career-center page calls). Each requisition
    # buries its externally-visible job id inside a generic key/value bag
    # (customFieldGroup.stringFields) rather than a top-level field.
    cid, cc_id = board_key.split("/")

    urls: list[str] = []
    skip = 0
    while len(urls) < _ADP_MAX_JOBS:
        response = httpx.get(
            _ADP_JOBS_URL,
            params={
                "cid": cid,
                "ccId": cc_id,
                "lang": "en_US",
                "locale": "en_US",
                "$top": _ADP_PAGE_SIZE,
                "$skip": skip,
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        requisitions = response.json().get("jobRequisitions", [])
        if not requisitions:
            break
        for req in requisitions:
            job_id = next(
                (
                    field["stringValue"]
                    for field in req.get("customFieldGroup", {}).get("stringFields", [])
                    if field.get("nameCode", {}).get("codeValue") == "ExternalJobID"
                ),
                None,
            )
            if job_id:
                urls.append(_ADP_JOB_URL.format(cid=cid, cc_id=cc_id, job_id=job_id))
        if len(requisitions) < _ADP_PAGE_SIZE:
            break
        skip += _ADP_PAGE_SIZE

    return urls[:_ADP_MAX_JOBS]


ADAPTER = AtsAdapter(
    AtsType.ADP,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=_board_url,
)
