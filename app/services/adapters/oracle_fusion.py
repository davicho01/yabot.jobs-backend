import re

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, RECENT_WINDOW_DAYS, TIMEOUT, AtsAdapter, get_with_retry, is_recent_posting

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


# No to_board_url — board_url is stored verbatim, exactly as submitted,
# rather than reconstructed from board_key. See board_url_for_key in
# app.services.ats_adapters.
ADAPTER = AtsAdapter(
    AtsType.ORACLE_FUSION,
    match=_match,
    fetch_jobs=_fetch_jobs,
)
