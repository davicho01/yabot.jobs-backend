import json
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from app.models.enums import AtsType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, limit_job_urls
from app.services.adapters.text import html_to_formatted_text

_HOST = "jobs.dayforcehcm.com"
# Board: jobs.dayforcehcm.com/{lang}/{clientNamespace}/{jobBoardCode}, job:
# .../{jobBoardCode}/jobs/{jobPostingId}; the language segment is optional
# (the site drops it after client-side navigation).
_URL_RE = re.compile(
    r"^https?://jobs\.dayforcehcm\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)(?:/jobs/(\d+))?/?(?:[?#]|$)"
)
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)


def _match(url: str) -> str | None:
    match = _URL_RE.match(url)
    return f"{match.group(1)}/{match.group(2)}" if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    # The board page is a Next.js shell; it lists jobs client-side via
    # POST /api/geo/{namespace}/jobposting/search, which needs the
    # X-CSRF-TOKEN that /api/auth/csrf hands every anonymous visitor (plus the
    # cookie it sets) — the same handshake the page itself does (verified
    # live: select, 129 jobs, 25 per call, paged by paginationStart).
    # robots.txt has no rules, only Cloudflare's content-signal comments.
    namespace, board = board_key.split("/")
    urls: list[str] = []
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        csrf = client.get(f"https://{_HOST}/api/auth/csrf")
        csrf.raise_for_status()
        token = csrf.json()["csrfToken"]
        start, total = 0, None
        while total is None or start < total:
            response = client.post(
                f"https://{_HOST}/api/geo/{namespace}/jobposting/search",
                json={
                    "clientNamespace": namespace,
                    "jobBoardCode": board,
                    "cultureCode": "en-US",
                    "distanceUnit": 0,
                    "paginationStart": start,
                },
                headers={"X-CSRF-TOKEN": token},
            )
            response.raise_for_status()
            data = response.json()
            postings = data.get("jobPostings") or []
            total = data.get("maxCount") or 0
            if not postings:
                break
            urls.extend(
                f"https://{_HOST}/en-US/{namespace}/{board}/jobs/{p['jobPostingId']}"
                for p in postings
                if p.get("jobPostingId")
            )
            start += len(postings)
            if len(urls) >= base.DEFAULT_MAX_JOBS_PER_CRAWL:
                break
    return limit_job_urls(urls)


def _site_name(page_props: dict[str, Any]) -> str | None:
    for query in (page_props.get("dehydratedState") or {}).get("queries", []):
        if (query.get("queryKey") or [None])[0] == "site-info":
            name = ((query.get("state") or {}).get("data") or {}).get("candidateCorrespondenceClientName")
            return name if isinstance(name, str) and name.strip() else None
    return None


def _posted_at(value: str | None):
    try:
        return datetime.fromisoformat(value).date() if value else None
    except ValueError:
        return None


def _scan_job_url(url: str) -> ScanResult | None:
    # Job pages publish no JSON-LD; the posting is in Next.js's __NEXT_DATA__
    # (pageProps.jobData), and the company name in the site-info query
    # (candidateCorrespondenceClientName — e.g. "Select Water Solutions").
    match = _URL_RE.match(url)
    if not match or not match.group(3):
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    next_data = _NEXT_DATA_RE.search(page.text)
    page_props = {}
    if next_data:
        page_props = (json.loads(next_data.group(1)).get("props") or {}).get("pageProps") or {}
    job = page_props.get("jobData")
    if not isinstance(job, dict) or not job.get("jobTitle"):
        return ScanResult(success=False, error="Dayforce job page has no jobData — removed or filled.", expired=True)
    # Closed postings stay reachable with full content (verified live: select
    # job 1, posted 2017, postingStatus 6); open ones are postingStatus 1 —
    # the only two values seen, so anything but 1 counts as closed.
    expiry = job.get("postingExpiryTimestampUTC")
    if job.get("postingStatus") != 1 or (expiry and datetime.fromisoformat(expiry) < datetime.now(timezone.utc)):
        return ScanResult(
            success=False, error=f"Dayforce posting is closed (postingStatus {job.get('postingStatus')}).", expired=True
        )
    content = job.get("jobPostingContent") or {}
    description = "\n\n".join(
        part
        for key in ("jobDescriptionHeader", "jobDescription", "jobDescriptionFooter")
        if (part := html_to_formatted_text(content.get(key)))
    )
    locations = [loc.get("formattedAddress") for loc in job.get("postingLocations") or [] if loc.get("formattedAddress")]
    return ScanResult(
        success=True,
        title=job["jobTitle"],
        description=description or None,
        company_name=_site_name(page_props),
        location="; ".join(dict.fromkeys(locations)) or None,
        workplace_type=WorkplaceType.REMOTE if job.get("hasVirtualLocation") and not locations else WorkplaceType.UNKNOWN,
        posted_at=_posted_at(job.get("postingStartTimestampUTC")),
    )


# No to_board_url: board_url is stored verbatim, as submitted.
ADAPTER = AtsAdapter(
    AtsType.DAYFORCE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    scan_job_url=_scan_job_url,
)
