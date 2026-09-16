from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter

_CLINCH_MAX_JOBS = 500
_CLINCH_SIGNATURE = "clinchtalent.com"


def _fetch_jobs(host: str) -> list[str]:
    # No public jobs API, but Clinch (a white-label career-site CMS — every
    # tenant runs on its own domain, there's no shared clinch.io host to
    # point at) publishes a standard sitemap.xml that cleanly separates job
    # postings (/jobs/{slug}) from marketing/blog pages (verified against a
    # live instance). Small volume in practice (~100 jobs), so no "today
    # only" filtering — just a safety cap like every other adapter's
    # _MAX_JOBS.
    response = httpx.get(f"https://{host}/sitemap.xml", timeout=TIMEOUT)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [
        loc.text
        for loc in root.findall(".//sm:loc", ns)
        if loc.text and urlsplit(loc.text).path.startswith("/jobs/")
    ]
    return urls[:_CLINCH_MAX_JOBS]


def _detect_embedded(url: str) -> str | None:
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    if _CLINCH_SIGNATURE not in response.text:
        return None
    return urlsplit(str(response.url)).netloc


def _board_key(url: str) -> str | None:
    return urlsplit(url).netloc or None


# Clinch has no static URL shape (match=None) — the tenant's own domain
# *is* the board, indistinguishable from any other company's careers page
# without fetching the page and checking for _CLINCH_SIGNATURE. board_key
# for listing is just the host, trivially recoverable from any stored
# board_url without redoing that signature check. No to_board_url —
# board_url is stored verbatim, same reasoning as Oracle Fusion.
ADAPTER = AtsAdapter(
    AtsType.CLINCH,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
    embedded_match=_detect_embedded,
)
