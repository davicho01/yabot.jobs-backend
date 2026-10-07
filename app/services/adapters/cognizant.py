import re
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from app.models.enums import AtsType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry, limit_job_urls

# careers.cognizant.com sits behind Cloudflare, which challenges browser-
# lookalike user agents (403, cf-mitigated: challenge) but, as verified
# 2026-10-07, serves real job pages to base.fetch_html's self-identified
# YabotJobsBot UA (with fetch_html's browser-render fallback for the requests
# it does challenge — it got the real page too) and sitemap.xml to a plain
# httpx request. robots.txt allows everything for "*".
_HOST_RE = re.compile(r"^https?://careers\.cognizant\.com(?:[:/?#]|$)", re.IGNORECASE)
_SITEMAP_URL = "https://careers.cognizant.com/sitemap.xml"
_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
# The sitemap lists every posting once per locale site (us-en, global-en,
# uki-en, india-en, ... — ten copies of ~1,850 jobs); us-en carries all of
# them, so it alone is the listing.
_US_JOB_RE = re.compile(r"^https://careers\.cognizant\.com/us-en/jobs/(\d+)/[^/]+/?$")
_JOB_PATH_RE = re.compile(r"^/[a-z]{2,6}-[a-z]{2}/[^/]+/\d+/", re.IGNORECASE)


def _match(url: str) -> str | None:
    return "cognizant" if _HOST_RE.match(url) else None


def _fetch_jobs(_board_key: str) -> list[str]:
    response = get_with_retry(_SITEMAP_URL, timeout=TIMEOUT)
    response.raise_for_status()
    jobs = [
        (entry.findtext("sm:loc", "", _SITEMAP_NS), entry.findtext("sm:lastmod", "", _SITEMAP_NS))
        for entry in ElementTree.fromstring(response.content).findall("sm:url", _SITEMAP_NS)
    ]
    jobs = [(loc, lastmod) for loc, lastmod in jobs if _US_JOB_RE.match(loc)]
    # Newest first, so the shared per-crawl cap keeps the freshest postings.
    jobs.sort(key=lambda job: job[1], reverse=True)
    return limit_job_urls(loc for loc, _ in jobs)


def _scan_job_url(url: str) -> ScanResult | None:
    # Read here rather than handed to the generic scanner: a challenged fetch
    # falls back to a browser render, which shouldn't be paid for twice, and
    # a removed job's 404 page must be recognized (not stored as a job).
    if not _HOST_RE.match(url) or not _JOB_PATH_RE.match(urlsplit(url).path):
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    postings = base.extract_json_ld_postings(page.text)
    if postings:
        return base.scan_result_from_job_ld(postings[0], page.text)
    if (base.fallback_title(page.text) or "").startswith("404"):
        # A removed (or unknown) job id renders "404 | Cognizant Careers"
        # (verified live on job 00099999999).
        return ScanResult(success=False, error="Cognizant job page is a 404 — removed or filled.", expired=True)
    # Anything else is an unrecognized page: fail and retry, never expire.
    return ScanResult(success=False, error="Cognizant job page has no JobPosting data.")


# No to_board_url: board_url is stored verbatim, as submitted.
ADAPTER = AtsAdapter(
    AtsType.COGNIZANT,
    match=_match,
    fetch_jobs=_fetch_jobs,
    scan_job_url=_scan_job_url,
)
