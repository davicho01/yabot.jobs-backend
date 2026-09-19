from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, get_with_retry

_CLINCH_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_CLINCH_SIGNATURE = "clinchtalent.com"


def _sitemap_job_urls(host: str) -> list[str]:
    response = get_with_retry(f"https://{host}/sitemap.xml", timeout=TIMEOUT)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    return [
        loc.text
        for loc in root.findall(".//sm:loc", ns)
        if loc.text and urlsplit(loc.text).path.startswith("/jobs/")
    ]


def _fetch_jobs(host: str) -> list[str]:
    # No public jobs API, but Clinch (a white-label career-site CMS — every
    # tenant runs on its own domain, there's no shared clinch.io host to
    # point at) publishes a standard sitemap.xml that cleanly separates job
    # postings (/jobs/{slug}) from marketing/blog pages (verified against a
    # live instance). Small volume in practice (~100 jobs), so no "today
    # only" filtering — just a safety cap like every other adapter's
    # _MAX_JOBS.
    return _sitemap_job_urls(host)[:_CLINCH_MAX_JOBS]


def _is_own_job_url(url: str, host: str) -> bool:
    # Real Clinch tenants list their own single-segment /jobs/{slug} pages.
    # Detection-only strictness (crawling stays lenient: existing rows such
    # as iCIMS-hosted ones use /jobs/{id}/{slug}/job): schooljobs.com's
    # sitemap is governmentjobs.com's (other host) and NEOGOV's own jobs are
    # /jobs/{id}-1/{slug}, and both were registered as active "clinch" rows.
    parts = urlsplit(url)
    return parts.netloc == host and len(parts.path.strip("/").split("/")) == 2


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
        return host if any(_is_own_job_url(u, host) for u in _sitemap_job_urls(host)) else None
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
