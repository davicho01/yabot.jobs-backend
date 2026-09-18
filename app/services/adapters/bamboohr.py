import re

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, limit_job_urls, AtsAdapter, get_with_retry

_BAMBOOHR_JOBS_URL = "https://{board_key}.bamboohr.com/careers/list"
_BAMBOOHR_JOB_URL = "https://{board_key}.bamboohr.com/careers/{job_id}"
_BAMBOOHR_URL_RE = re.compile(r"([a-zA-Z0-9-]+)\.bamboohr\.com", re.IGNORECASE)


def _match(url: str) -> str | None:
    match = _BAMBOOHR_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    # Free, public, unauthenticated API — no key required. The list response
    # doesn't include a direct URL, but each posting's page is a predictable
    # /careers/{id} path off the same subdomain.
    response = get_with_retry(_BAMBOOHR_JOBS_URL.format(board_key=board_key), timeout=TIMEOUT)
    response.raise_for_status()
    postings = response.json().get("result", [])
    return limit_job_urls(
        _BAMBOOHR_JOB_URL.format(board_key=board_key, job_id=posting["id"])
        for posting in postings
        if posting.get("id")
    )


ADAPTER = AtsAdapter(
    AtsType.BAMBOOHR,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://{key}.bamboohr.com/careers",
)
