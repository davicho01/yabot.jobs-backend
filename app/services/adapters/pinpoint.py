from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, get_with_retry

_PINPOINT_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_PINPOINT_SIGNATURE = "pinpointhq"
# Every Pinpoint board publishes a standard jobs.rss feed (verified live:
# careers.manutd.com) — each <item><link> is the real, current posting URL
# on the board's own domain, even though the feed's channel-level <link>
# points at a different-looking legacy subdomain; item links are what
# matters.
_JOBS_RSS_PATH = "/jobs.rss"


def _detect_embedded(url: str) -> str | None:
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    if _PINPOINT_SIGNATURE not in response.text.lower():
        return None
    return urlsplit(str(response.url)).netloc or None


def _fetch_jobs(host: str) -> list[str]:
    response = get_with_retry(f"https://{host}{_JOBS_RSS_PATH}", timeout=TIMEOUT)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    urls = [
        link.text.strip()
        for link in root.findall(".//item/link")
        if link.text and urlsplit(link.text.strip()).netloc == host
    ]
    return urls[:_PINPOINT_MAX_JOBS]


def _board_key(url: str) -> str | None:
    return urlsplit(url).netloc or None


# No to_board_url — the company's own domain is the board, board_url is
# stored verbatim, same reasoning as Avature/Paradox.
ADAPTER = AtsAdapter(
    AtsType.PINPOINT,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
    embedded_match=_detect_embedded,
)
