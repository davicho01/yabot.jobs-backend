import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter, is_recent_posting, limit_job_urls, post_with_retry

_JOBS_URL = "https://jobs.gem.com/api/public/graphql"
_QUERY = """query JobBoardList($boardId: String!) {
  oatsExternalJobPostings(boardId: $boardId) {
    jobPostings { extId firstPublishedTsSec }
  }
}"""


def _match(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.hostname != "jobs.gem.com":
        return None
    slug = parts.path.strip("/").split("/")[0]
    return slug if re.fullmatch(r"[a-zA-Z0-9_-]+", slug) and slug != "api" else None


def _published_at(job: dict) -> datetime | None:
    timestamp = job.get("firstPublishedTsSec")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _fetch_jobs(board_key: str) -> list[str]:
    # The public board's own GraphQL query returns the complete listing;
    # firstPublishedTsSec is available on the same records without fetching
    # each detail page. HTTP 200 can still contain GraphQL errors.
    response = post_with_retry(
        _JOBS_URL, json={"query": _QUERY, "variables": {"boardId": board_key}}, timeout=TIMEOUT
    )
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        raise ValueError(f"Gem job listing failed: {body['errors']}")
    data = body.get("data") or {}
    listing = data.get("oatsExternalJobPostings")
    if not isinstance(listing, dict) or not isinstance(listing.get("jobPostings"), list):
        raise ValueError("Gem response is missing jobPostings")
    jobs = listing["jobPostings"]
    return limit_job_urls(
        f"https://jobs.gem.com/{board_key}/{job['extId']}"
        for job in jobs
        if isinstance(job.get("extId"), str)
        and re.fullmatch(r"[a-zA-Z0-9_-]+", job["extId"])
        and is_recent_posting(_published_at(job))
    )


ADAPTER = AtsAdapter(
    AtsType.GEM,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://jobs.gem.com/{key}",
)
