import re
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, get_with_retry

_ZENATS_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_ZENATS_SIGNATURE = "zenats"
# The board root server-renders every open posting as a plain
# /position/{slug}/ anchor (verified live: jobs.halan.com, 16 postings, no
# pagination control found on the page).
_JOB_HREF_RE = re.compile(r'href="(https?://[^"?]+/position/[^"?]+/)"')


def _detect_embedded(url: str) -> str | None:
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    if _ZENATS_SIGNATURE not in response.text.lower():
        return None
    return urlsplit(str(response.url)).netloc or None


def _fetch_jobs(host: str) -> list[str]:
    response = get_with_retry(f"https://{host}", timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    urls = dict.fromkeys(_JOB_HREF_RE.findall(response.text))
    return list(urls)[:_ZENATS_MAX_JOBS]


def _board_key(url: str) -> str | None:
    return urlsplit(url).netloc or None


# No to_board_url — the company's own domain is the board, board_url is
# stored verbatim, same reasoning as Avature/Paradox.
ADAPTER = AtsAdapter(
    AtsType.ZENATS,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
    embedded_match=_detect_embedded,
)
