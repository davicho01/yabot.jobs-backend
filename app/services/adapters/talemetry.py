import re
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, get_with_retry
from app.services.browser_fetch import fetch_rendered_page

_TALEMETRY_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_TALEMETRY_SIGNATURE = "talemetry"
_JOB_LINK_RE = re.compile(r'href="(https?://[^"?]+/jobs/\d+-[^"?]+/?)"', re.IGNORECASE)
_PAGE_SIZE = 25  # observed tenant-configured default (verified: Progressive)


def _fetch_page_html(url: str) -> str | None:
    try:
        response = get_with_retry(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        if response.text.strip():
            return response.text
    except httpx.HTTPError:
        pass
    # Some tenants (verified live: careers.progressive.com) front every path
    # with a WAF that returns a plain 403 to any httpx request, even with a
    # real browser User-Agent — a real browser render is the only way
    # through, same reasoning as avature.py's delta.avature.net case.
    rendered = fetch_rendered_page(url)
    return rendered.html if rendered else None


def _detect_embedded(url: str) -> str | None:
    html = _fetch_page_html(url)
    if html is None or _TALEMETRY_SIGNATURE not in html.lower():
        return None
    return urlsplit(url).netloc or None


def _fetch_jobs(host: str) -> list[str]:
    urls: list[str] = []
    page = 1
    while len(urls) < _TALEMETRY_MAX_JOBS:
        html = _fetch_page_html(f"https://{host}/search/jobs/?page={page}&q=")
        if html is None:
            break
        links = dict.fromkeys(_JOB_LINK_RE.findall(html))
        if not links:
            break
        urls.extend(links)
        if len(links) < _PAGE_SIZE:
            break
        page += 1
    return urls[:_TALEMETRY_MAX_JOBS]


def _board_key(url: str) -> str | None:
    return urlsplit(url).netloc or None


# No to_board_url — the company's own domain is the board, board_url is
# stored verbatim, same reasoning as Avature/Paradox.
ADAPTER = AtsAdapter(
    AtsType.TALEMETRY,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
    embedded_match=_detect_embedded,
)
