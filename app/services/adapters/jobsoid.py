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

# {tenant}.jobsoid.com serves every open job at /api/v1/jobs as a JSON list
# with its hostedUrl and postedDate (verified 2026-11-01, vibrus: 3 jobs).
# robots.txt disallows only the site's asset folders. Job pages carry
# JobPosting JSON-LD, so scans go through the generic scanner.
_HOST_RE = re.compile(r"([a-z0-9][a-z0-9-]*)\.jobsoid\.com", re.IGNORECASE)
_NOT_TENANTS = {"www", "api", "app"}


def _match(url: str) -> str | None:
    match = _HOST_RE.search(url)
    if match is None or match.group(1).lower() in _NOT_TENANTS:
        return None
    return f"{match.group(1).lower()}.jobsoid.com"


def _fetch_jobs(host: str) -> BoardListing:
    response = get_with_retry(f"https://{host}/api/v1/jobs", timeout=TIMEOUT)
    response.raise_for_status()
    jobs = response.json()
    if not isinstance(jobs, list):
        raise ValueError(f"Jobsoid board {host!r} returned no job list.")
    urls = []
    posted = {}
    for job in jobs:
        url = job.get("hostedUrl")
        if url:
            urls.append(url)
            if (day := posting_date(job.get("postedDate"))) is not None:
                posted[url] = day
    return BoardListing(limit_job_urls(urls), posted=posted)


ADAPTER = AtsAdapter(
    AtsType.JOBSOID,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://{key}",
)
