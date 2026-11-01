import re

from app.models.enums import AtsType
from app.services.adapters.base import (
    TIMEOUT,
    AtsAdapter,
    BoardListing,
    get_with_retry,
    limit_job_urls,
    posting_date,
)

# Boards are careers.jobscore.com/careers/{slug}. Each has a JSON feed at
# .../feed.json listing every open job with its detail URL and opened date
# (verified 2026-11-01, ierustechnologiesinc). robots.txt only disallows
# /apply_flow/. Job pages carry JobPosting JSON-LD, so scans go through the
# generic scanner.
_BOARD_RE = re.compile(r"careers\.jobscore\.com/careers/([A-Za-z0-9_-]+)", re.IGNORECASE)
_FEED_URL = "https://careers.jobscore.com/careers/{slug}/feed.json"


def _match(url: str) -> str | None:
    match = _BOARD_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(slug: str) -> BoardListing:
    response = get_with_retry(_FEED_URL.format(slug=slug), timeout=TIMEOUT)
    response.raise_for_status()
    data = response.json()
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        raise ValueError(f"JobScore board {slug!r} returned no job feed.")
    urls = []
    posted = {}
    for job in jobs:
        # The feed's detail_url carries tracking params; the path is the job.
        url = (job.get("detail_url") or "").split("?")[0]
        if url:
            urls.append(url)
            if (day := posting_date(job.get("opened_date"))) is not None:
                posted[url] = day
    return BoardListing(limit_job_urls(urls), posted=posted)


ADAPTER = AtsAdapter(
    AtsType.JOBSCORE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://careers.jobscore.com/careers/{key}",
)
