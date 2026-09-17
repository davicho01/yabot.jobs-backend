import json
import re
from datetime import datetime, timezone

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter, get_with_retry, parse_month_day_year

_APPLE_JOBS_URL = "https://jobs.apple.com/en-us/search"
_APPLE_JOB_URL = "https://jobs.apple.com/en-us/details/{position_id}/{slug}"
_APPLE_HYDRATION_RE = re.compile(r'window\.__staticRouterHydrationData = JSON\.parse\("(.*?)"\);', re.DOTALL)
_APPLE_PAGE_SIZE = 20
_APPLE_MAX_JOBS = 200
_APPLE_URL_RE = re.compile(r"jobs\.apple\.com", re.IGNORECASE)


def _match(url: str) -> str | None:
    return "apple" if _APPLE_URL_RE.search(url) else None


def _fetch_jobs(board_key: str) -> list[str]:  # noqa: ARG001 - single-company board, fixed key, no key needed
    # No public API — the search page server-renders full job data into a
    # `window.__staticRouterHydrationData = JSON.parse("...")` blob (a React
    # Router hydration payload): a double-escaped JSON string, more fragile
    # than a real endpoint (same caveat as JazzHR) but the data itself is
    # clean structured JSON, not raw HTML to regex-scrape. sort=newest
    # verified newest-first (page 1 was entirely today's postingDate, page
    # 10 was already yesterday's), so the same "today only" early-exit as
    # Workday/Amazon applies — using postingDate (a stable per-job date),
    # NOT postDateInGMT, which turned out to change on every request for
    # the same job (a live response timestamp, not a stored value).
    urls: list[str] = []
    today = datetime.now(timezone.utc).date()
    page = 1
    while len(urls) < _APPLE_MAX_JOBS:
        response = get_with_retry(_APPLE_JOBS_URL, params={"sort": "newest", "page": page}, timeout=TIMEOUT)
        response.raise_for_status()
        match = _APPLE_HYDRATION_RE.search(response.text)
        if not match:
            break
        # The blob is a JS string literal fed to JSON.parse — re-wrapping it
        # in quotes and parsing through json.loads (rather than the
        # `unicode_escape` codec) unescapes it correctly without mangling
        # any real non-ASCII characters already in the text.
        search_data = json.loads(json.loads('"' + match.group(1) + '"'))["loaderData"]["search"]
        postings = search_data.get("searchResults", [])
        if not postings:
            break

        todays_postings = [
            posting for posting in postings if parse_month_day_year(posting.get("postingDate"), month_style="%b %d, %Y") == today
        ]
        urls.extend(
            _APPLE_JOB_URL.format(position_id=posting["positionId"], slug=posting["transformedPostingTitle"])
            for posting in todays_postings
            if posting.get("positionId") and posting.get("transformedPostingTitle")
        )
        if len(todays_postings) < len(postings) or len(postings) < _APPLE_PAGE_SIZE:
            break
        page += 1

    return urls[:_APPLE_MAX_JOBS]


ADAPTER = AtsAdapter(
    AtsType.APPLE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://jobs.apple.com",
)
