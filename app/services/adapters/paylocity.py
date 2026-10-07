import json
import re
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry, limit_job_urls

# Every tenant's board is on Paylocity's own host — recruiting.paylocity.com,
# or a numbered shard of it (Paylocity's own board is 2000recruiting.
# paylocity.com) — identified by a module GUID: /recruiting/jobs/All/{guid}
# (an optional trailing /{Company-Name} slug is cosmetic). The GUID only
# resolves on the shard that issued it, so the host stays part of the key.
_BOARD_URL_RE = re.compile(
    r"^https?://(\d*recruiting\.paylocity\.com)/recruiting/jobs/(?:all|list)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)
_JOB_URL_RE = re.compile(r"^https?://(\d*recruiting\.paylocity\.com)/recruiting/jobs/details/\d+", re.IGNORECASE)
# A job page links back to its board ("View all jobs") — the only place a
# job URL's module GUID shows up.
_BOARD_LINK_RE = re.compile(r"/recruiting/jobs/all/([0-9a-f-]{36})", re.IGNORECASE)


def _match(url: str) -> str | None:
    match = _BOARD_URL_RE.match(url)
    return f"{match.group(1).lower()}/{match.group(2).lower()}" if match else None


def _detect_embedded(url: str) -> str | None:
    job_match = _JOB_URL_RE.match(url)
    if not job_match:
        return None
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    board = _BOARD_LINK_RE.search(response.text)
    return f"{job_match.group(1).lower()}/{board.group(1).lower()}" if board else None


def _board_url(board_key: str) -> str:
    host, guid = board_key.split("/")
    return f"https://{host}/recruiting/jobs/All/{guid}"


def _fetch_jobs(board_key: str) -> list[str]:
    # The board page is server-rendered with the whole job list embedded as
    # window.pageData = {..., "Jobs": [...]} (verified live: GBC Food
    # Services, 37 jobs in one page, no pagination, each with JobId,
    # PublishedDate and IsInternal). No robots.txt on the host (404).
    host = board_key.split("/")[0]
    response = get_with_retry(_board_url(board_key), timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    marker = response.text.find("window.pageData")
    if marker == -1:
        raise ValueError(f"No window.pageData on Paylocity board {board_key!r}")
    page_data, _ = json.JSONDecoder().raw_decode(response.text, response.text.index("{", marker))
    jobs = [job for job in page_data.get("Jobs") or [] if job.get("JobId") and not job.get("IsInternal")]
    jobs.sort(key=lambda job: job.get("PublishedDate") or "", reverse=True)
    return limit_job_urls(f"https://{host}/Recruiting/Jobs/Details/{job['JobId']}" for job in jobs)


def _scan_job_url(url: str) -> ScanResult | None:
    # A filled/removed posting 302s to /Recruiting/Jobs/JobNotFound, a 200
    # page titled "Job Not Found" — which the generic scanner would store as
    # a job by that name (verified live: 2000recruiting.paylocity.com job
    # 46951). Check the redirect without following it; a live posting falls
    # through (None) to the generic scanner, which reads its JobPosting
    # JSON-LD fine.
    if not _JOB_URL_RE.match(url):
        return None
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=False)
    except httpx.HTTPError:
        return None
    if response.is_redirect and "jobnotfound" in response.headers.get("location", "").lower():
        return ScanResult(
            success=False, error="Paylocity redirected to JobNotFound — this posting has been removed or filled.",
            expired=True,
        )
    return None


# No to_board_url: board_url is stored verbatim, as submitted (a board URL
# or a job URL) — board_key re-derives the key from either.
ADAPTER = AtsAdapter(
    AtsType.PAYLOCITY,
    match=_match,
    board_key=lambda url: _match(url) or _detect_embedded(url),
    fetch_jobs=_fetch_jobs,
    embedded_match=_detect_embedded,
    scan_job_url=_scan_job_url,
)
