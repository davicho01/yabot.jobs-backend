import re

from app.models.enums import AtsType
from app.services.adapters.base import (
    DEFAULT_MAX_JOBS_PER_CRAWL,
    RECENT_WINDOW_DAYS,
    TIMEOUT,
    AtsAdapter,
    get_with_retry,
    is_recent_posting,
    parse_month_day_year,
)

_AMAZON_JOBS_URL = "https://www.amazon.jobs/en/search.json"
_AMAZON_JOB_BASE_URL = "https://www.amazon.jobs"
_AMAZON_PAGE_SIZE = 100
_AMAZON_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_AMAZON_URL_RE = re.compile(r"amazon\.jobs", re.IGNORECASE)


def _match(url: str) -> str | None:
    return "amazon" if _AMAZON_URL_RE.search(url) else None


def _fetch_jobs(board_key: str) -> list[str]:  # noqa: ARG001 - single-company board, fixed key, no key needed
    # Free, public, unauthenticated API — no key required. sort=recent
    # verified newest-first (offset=0 was entirely today's date, offset=200
    # was already yesterday's) — same RECENT_WINDOW_DAYS early-exit as
    # Workday.
    urls: list[str] = []
    offset = 0
    while len(urls) < _AMAZON_MAX_JOBS:
        response = get_with_retry(
            _AMAZON_JOBS_URL,
            params={"result_limit": _AMAZON_PAGE_SIZE, "offset": offset, "sort": "recent"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        jobs = response.json().get("jobs", [])
        if not jobs:
            break

        recent_jobs = [
            job
            for job in jobs
            if (posted := parse_month_day_year(job.get("posted_date"), month_style="%B %d, %Y")) is not None
            and is_recent_posting(posted)
        ]
        urls.extend(_AMAZON_JOB_BASE_URL + job["job_path"] for job in recent_jobs if job.get("job_path"))
        if len(recent_jobs) < len(jobs) or len(jobs) < _AMAZON_PAGE_SIZE:
            break
        offset += _AMAZON_PAGE_SIZE

    return urls[:_AMAZON_MAX_JOBS]


ADAPTER = AtsAdapter(
    AtsType.AMAZON,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://www.amazon.jobs",
)
