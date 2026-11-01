import re

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import (
    TIMEOUT,
    AtsAdapter,
    BoardListing,
    ScanResult,
    extract_json_ld_postings,
    fetch_html,
    get_with_retry,
    limit_job_urls,
    max_jobs_per_crawl,
    scan_result_from_job_ld,
)

# Every company's board is careers.hireology.com/{slug}. The page itself is an
# empty shell that its script fills from api.hireology.com; that public endpoint
# answers without the page's token (verified 2026-11-01, s3hotelgroup: 17 open
# jobs) and 404s for an unknown slug. Job pages carry JobPosting JSON-LD, so
# scans go through the generic scanner.
_BOARD_RE = re.compile(r"careers\.hireology\.com/([A-Za-z0-9_-]+)", re.IGNORECASE)
_RESERVED = {"api", "assets", "sitemap_index", "sitemaps", "robots"}  # the slug stops at the "."
_API_URL = "https://api.hireology.com/v2/public/careers/{slug}"
_PAGE_SIZE = 100


def _match(url: str) -> str | None:
    match = _BOARD_RE.search(url)
    if match is None or match.group(1).lower() in _RESERVED:
        return None
    return match.group(1)


# A job page's JSON-LD validThrough is always its created date + 30 days, not a
# real close date: 10 of s3hotelgroup's 17 jobs (all listed "Open" by the API)
# were past it on 2026-11-01. The generic scanner would mark those expired and
# close them every crawl, so the scan reads the JSON-LD without it. A removed
# job answers 404/410, which fetch_html reports as gone.
_JOB_URL_RE = re.compile(r"careers\.hireology\.com/[A-Za-z0-9_-]+/\d+", re.IGNORECASE)


def scan_job_url(url: str) -> ScanResult | None:
    if not _JOB_URL_RE.search(url):
        return None
    try:
        page = fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    postings = extract_json_ld_postings(page.text)
    if not postings:
        return None  # not a posting page: leave it to the generic scanner
    return scan_result_from_job_ld({k: v for k, v in postings[0].items() if k != "validThrough"}, page.text)


def _fetch_jobs(slug: str) -> "list[str] | BoardListing":
    urls: list[str] = []
    page = 1
    while len(urls) < max_jobs_per_crawl():
        response = get_with_retry(
            _API_URL.format(slug=slug), params={"page": page, "page_size": _PAGE_SIZE}, timeout=TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        jobs = data.get("data") if isinstance(data, dict) else None
        if not isinstance(jobs, list):
            raise ValueError(f"Hireology board {slug!r} returned no job list.")
        urls.extend(job["career_site_url"] for job in jobs if job.get("career_site_url"))
        if not jobs or len(urls) >= (data.get("count") or 0):
            break
        page += 1
    return limit_job_urls(urls)


ADAPTER = AtsAdapter(
    AtsType.HIREOLOGY,
    match=_match,
    fetch_jobs=_fetch_jobs,
    scan_job_url=scan_job_url,
    to_board_url=lambda key: f"https://careers.hireology.com/{key}",
)
