import re

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter

_GOOGLE_JOBS_URL = "https://www.google.com/about/careers/applications/jobs/results"
_GOOGLE_JOB_RE = re.compile(r'href="jobs/results/([^"?]+)')
# Google has no per-job posting-date field anywhere (listing or detail
# page), unlike Workday/Amazon/Apple, so there's no way to reliably stop at
# "today's postings only" — this just takes the first few pages under
# sort_by=date as a heuristic recent-enough window (already-known URLs are
# deduped either way, same rationale as the safety caps used elsewhere).
_GOOGLE_PAGE_SIZE = 20
_GOOGLE_MAX_PAGES = 10
_GOOGLE_URL_RE = re.compile(r"google\.com/about/careers", re.IGNORECASE)


def _match(url: str) -> str | None:
    return "google" if _GOOGLE_URL_RE.search(url) else None


def _fetch_jobs(board_key: str) -> list[str]:  # noqa: ARG001 - single-company board, fixed key, no key needed
    # No public API — the search-results page server-renders real job links
    # directly into static HTML (no JS execution needed), verified against
    # 3362 live postings, 20 per page. sort_by=date changes result order
    # (verified against the unsorted default) but there's no per-job date
    # anywhere to confirm it's a true chronological cutoff — see
    # _GOOGLE_MAX_PAGES above for why this only takes a bounded number of
    # pages rather than doing a Workday-style exact "today only" stop.
    urls: list[str] = []
    for page in range(1, _GOOGLE_MAX_PAGES + 1):
        response = httpx.get(_GOOGLE_JOBS_URL, params={"page": page, "sort_by": "date"}, timeout=TIMEOUT)
        response.raise_for_status()
        job_paths = dict.fromkeys(_GOOGLE_JOB_RE.findall(response.text))  # dedupe, keep order
        if not job_paths:
            break
        urls.extend(f"{_GOOGLE_JOBS_URL}/{path}" for path in job_paths)
        if len(job_paths) < _GOOGLE_PAGE_SIZE:
            break
    return urls


ADAPTER = AtsAdapter(
    AtsType.GOOGLE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://www.google.com/about/careers",
)
