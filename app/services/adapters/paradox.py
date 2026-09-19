import re
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, get_with_retry

_PARADOX_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_PARADOX_SIGNATURE = "paradox.ai"
_JOB_HREF_RE = re.compile(r'href="(/[^"]+/job/[^"]+)"')


def _detect_embedded(url: str) -> str | None:
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    if _PARADOX_SIGNATURE not in response.text:
        return None
    return urlsplit(str(response.url)).netloc


def _fetch_jobs(host: str) -> list[str]:
    # Paradox (an AI-chatbot recruiting vendor — "Olivia") also hosts a
    # plain server-rendered career site per tenant on the tenant's own
    # domain, no shared paradox.ai host to point at (white-label, like
    # Attrax/Clinch). /jobs/page/{n} paginates 10 postings/page as ordinary
    # HTML — verified live: careers.marriott.com, ~14.5k jobs across ~1446
    # pages, page 1447 (past the end) 302s back to / with no job links, a
    # clean stop signal with no need to track the last valid page number.
    # Listing cards carry no posting date at all (only the per-job page's
    # schema.org JobPosting JSON-LD does) — same tradeoff as attrax.py/
    # clinch.py, no recency filtering, just the shared cap.
    urls: list[str] = []
    page = 1
    while len(urls) < _PARADOX_MAX_JOBS:
        response = get_with_retry(f"https://{host}/jobs/page/{page}", timeout=TIMEOUT)
        response.raise_for_status()
        paths = dict.fromkeys(_JOB_HREF_RE.findall(response.text))
        if not paths:
            break
        urls.extend(f"https://{host}{path}" for path in paths)
        page += 1
    return urls[:_PARADOX_MAX_JOBS]


# White-label onto each tenant's own domain, like Attrax/Clinch/Oracle
# Fusion — no shared host to canonicalize to, board_url stored verbatim.
ADAPTER = AtsAdapter(
    AtsType.PARADOX,
    fetch_jobs=_fetch_jobs,
    board_key=_detect_embedded,
    embedded_match=_detect_embedded,
)
