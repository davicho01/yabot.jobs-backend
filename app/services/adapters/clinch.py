from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter, get_with_retry

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
    response = get_with_retry(f"https://{host}/sitemap.xml", timeout=TIMEOUT)
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
    host = urlsplit(str(response.url)).netloc
    if _CLINCH_SIGNATURE in response.text:
        return host
    # Some tenants front their marketing pages with bot-protection (AWS WAF
    # Bot Control, verified against careers.upstart.com) that challenges a
    # plain httpx fetch — no signature string, no job content, just an
    # empty 202 — even though sitemap.xml (what _fetch_jobs actually reads
    # day to day) sits behind no such protection. A sitemap that genuinely
    # contains /jobs/ postings is just as strong a signal as the marketing
    # page's signature string, so fall back to it.
    try:
        return host if _fetch_jobs(host) else None
    except (httpx.HTTPError, ElementTree.ParseError):
        return None


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
