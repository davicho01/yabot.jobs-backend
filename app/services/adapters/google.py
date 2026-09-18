import re

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, get_with_retry, limit_job_urls

_GOOGLE_JOBS_URL = "https://www.google.com/about/careers/applications/jobs/results"
_GOOGLE_JOB_RE = re.compile(r'href="jobs/results/([^"?]+)')
# Google has no per-job posting-date field anywhere (listing or detail
# page), unlike Workday/Amazon/Apple, so there's no way to reliably stop at
# a precise date cutoff. Pagination is bounded by the shared job cap.
_GOOGLE_PAGE_SIZE = 20
_GOOGLE_URL_RE = re.compile(r"google\.com/about/careers", re.IGNORECASE)


def _match(url: str) -> str | None:
    return "google" if _GOOGLE_URL_RE.search(url) else None


def _fetch_jobs(board_key: str) -> list[str]:  # noqa: ARG001 - single-company board, fixed key, no key needed
    # No public API — the search-results page server-renders real job links
    # directly into static HTML (no JS execution needed), verified against
    # 3362 live postings, 20 per page. sort_by=date changes result order
    # (verified against the unsorted default) but there's no per-job date
    # anywhere to confirm it's a true chronological cutoff.
    urls: list[str] = []
    for page in range(1, (DEFAULT_MAX_JOBS_PER_CRAWL + _GOOGLE_PAGE_SIZE - 1) // _GOOGLE_PAGE_SIZE + 1):
        response = get_with_retry(_GOOGLE_JOBS_URL, params={"page": page, "sort_by": "date"}, timeout=TIMEOUT)
        response.raise_for_status()
        job_paths = dict.fromkeys(_GOOGLE_JOB_RE.findall(response.text))  # dedupe, keep order
        if not job_paths:
            break
        urls.extend(f"{_GOOGLE_JOBS_URL}/{path}" for path in job_paths)
        if len(job_paths) < _GOOGLE_PAGE_SIZE:
            break
    return limit_job_urls(urls)


ADAPTER = AtsAdapter(
    AtsType.GOOGLE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://www.google.com/about/careers",
)
