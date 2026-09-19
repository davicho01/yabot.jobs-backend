import re

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, get_with_retry

_PEOPLEADMIN_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_PEOPLEADMIN_URL_RE = re.compile(r"([a-zA-Z0-9-]+\.peopleadmin\.com)", re.IGNORECASE)
_JOB_HREF_RE = re.compile(r'href="/postings/(\d+)"')
_PAGE_SIZE = 60  # observed tenant-configured default (verified: San Jose Evergreen CCD)


def _match(url: str) -> str | None:
    match = _PEOPLEADMIN_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(host: str) -> list[str]:
    urls: list[str] = []
    page = 1
    while len(urls) < _PEOPLEADMIN_MAX_JOBS:
        response = get_with_retry(f"https://{host}/postings/search", params={"page": page}, timeout=TIMEOUT)
        response.raise_for_status()
        ids = dict.fromkeys(_JOB_HREF_RE.findall(response.text))
        if not ids:
            break
        urls.extend(f"https://{host}/postings/{job_id}" for job_id in ids)
        if len(ids) < _PAGE_SIZE:
            break
        page += 1
    return urls[:_PEOPLEADMIN_MAX_JOBS]


def _board_key(url: str) -> str | None:
    match = _PEOPLEADMIN_URL_RE.search(url)
    return match.group(1) if match else None


# No to_board_url — {tenant}.peopleadmin.com is already the canonical
# shared host, board_url is stored verbatim.
ADAPTER = AtsAdapter(
    AtsType.PEOPLEADMIN,
    match=_match,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
)
