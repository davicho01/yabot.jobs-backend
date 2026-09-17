import re
from datetime import datetime, timezone

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter, get_with_retry, parse_month_day_year

_AMAZON_JOBS_URL = "https://www.amazon.jobs/en/search.json"
_AMAZON_JOB_BASE_URL = "https://www.amazon.jobs"
_AMAZON_PAGE_SIZE = 100
_AMAZON_MAX_JOBS = 200
_AMAZON_URL_RE = re.compile(r"amazon\.jobs", re.IGNORECASE)


def _match(url: str) -> str | None:
    return "amazon" if _AMAZON_URL_RE.search(url) else None


def _fetch_jobs(board_key: str) -> list[str]:  # noqa: ARG001 - single-company board, fixed key, no key needed
    # Free, public, unauthenticated API — no key required. sort=recent
    # verified newest-first (offset=0 was entirely today's date, offset=200
    # was already yesterday's) — same "today only" early-exit as Workday,
    # since Amazon has 10,000+ open roles.
    urls: list[str] = []
    offset = 0
    today = datetime.now(timezone.utc).date()
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

        todays_jobs = [
            job for job in jobs if parse_month_day_year(job.get("posted_date"), month_style="%B %d, %Y") == today
        ]
        urls.extend(_AMAZON_JOB_BASE_URL + job["job_path"] for job in todays_jobs if job.get("job_path"))
        if len(todays_jobs) < len(jobs) or len(jobs) < _AMAZON_PAGE_SIZE:
            break
        offset += _AMAZON_PAGE_SIZE

    return urls[:_AMAZON_MAX_JOBS]


ADAPTER = AtsAdapter(
    AtsType.AMAZON,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://www.amazon.jobs",
)
