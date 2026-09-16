import re
from urllib.parse import parse_qs, urlsplit

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter

_ADP_JOBS_URL = "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"
_ADP_JOB_URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
    "?cid={cid}{cc_id_param}&type=JS&lang=en_US&selectedMenuKey=CareerCenter&jobId={job_id}"
)
_ADP_PAGE_SIZE = 50
# ADP client career sites (one company's own postings) run nowhere near
# Amazon/Google scale — no "today only" early-exit needed, just a safety cap.
_ADP_MAX_JOBS = 500
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


def _fetch_jobs(board_key: str) -> list[str]:
    # Free, public, unauthenticated API — no key required (the same feed
    # ADP's own JS-rendered career-center page calls). Each requisition
    # buries its externally-visible job id inside a generic key/value bag
    # (customFieldGroup.stringFields) rather than a top-level field.
    cid, cc_id = board_key.split("/")
    cc_id_param = {"ccId": cc_id} if cc_id else {}
    cc_id_url_param = f"&ccId={cc_id}" if cc_id else ""

    urls: list[str] = []
    skip = 0
    while len(urls) < _ADP_MAX_JOBS:
        response = httpx.get(
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
                urls.append(_ADP_JOB_URL.format(cid=cid, cc_id_param=cc_id_url_param, job_id=job_id))
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
