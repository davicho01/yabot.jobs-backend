import re

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, get_with_retry

_PEOPLEADMIN_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_PEOPLEADMIN_URL_RE = re.compile(r"([a-zA-Z0-9-]+\.peopleadmin\.com)", re.IGNORECASE)
_JOB_HREF_RE = re.compile(r'href="/postings/(\d+)"')


def _match(url: str) -> str | None:
    match = _PEOPLEADMIN_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(host: str) -> list[str]:
    # Page size is tenant-configured (60 at San Jose Evergreen CCD, 30 at
    # utah/weberstate-sb), so a short page is judged against page 1's size,
    # not a fixed constant — a fixed 60 silently stopped 30-per-page tenants
    # after page 1.
    seen: dict[str, None] = {}
    page_size = None
    page = 1
    while len(seen) < _PEOPLEADMIN_MAX_JOBS:
        response = get_with_retry(f"https://{host}/postings/search", params={"page": page}, timeout=TIMEOUT)
        response.raise_for_status()
        ids = dict.fromkeys(_JOB_HREF_RE.findall(response.text))
        new_ids = [job_id for job_id in ids if job_id not in seen]
        if not new_ids:
            break
        seen.update(dict.fromkeys(new_ids))
        page_size = page_size or len(ids)
        if len(ids) < page_size:
            break
        page += 1
    return [f"https://{host}/postings/{job_id}" for job_id in seen][:_PEOPLEADMIN_MAX_JOBS]


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
