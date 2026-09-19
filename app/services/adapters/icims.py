import re
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import (
    DEFAULT_MAX_JOBS_PER_CRAWL,
    TIMEOUT,
    AtsAdapter,
    get_with_retry,
    is_recent_posting,
)

_ICIMS_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_ICIMS_URL_RE = re.compile(r"([a-zA-Z0-9-]+\.icims\.com)", re.IGNORECASE)

# iCIMS actually ships (at least) two very different products under one
# brand, verified live against nine real tenants:
#
# - "Classic": {tenant}.icims.com/jobs/search?ss=1&in_iframe=1 is itself a
#   server-rendered listing (job links -> /jobs/{id}/{slug}/job?in_iframe=1,
#   which — unlike the same path without in_iframe=1, verified live — embeds
#   a real schema.org JobPosting JSON-LD block the generic scanner already
#   picks up). Paginates via &pr={page}, page size 50 (verified Schwab/Joby
#   Aviation).
# - "Jibe/Attract" (Jibe was acquired by iCIMS, now its modern career-site
#   CMS): the .icims.com tenant subdomain is just a stub whose entire body
#   is `window.top.location.href = '{vanity}'` — no content of its own. The
#   real board lives on the company's own vanity domain (careers.costco.com,
#   jobs.keysight.com) and exposes a public, unauthenticated JSON API at
#   {vanity}/api/jobs — same API some *already*-white-labeled vanity domains
#   (careers.ellucian.com, careers.mheducation.com) expose directly with no
#   .icims.com hop needed at all. Each job's own `apply_url` field points
#   back to a login-walled `{tenant}.icims.com/jobs/{id}/login` page, but the
#   vanity domain also serves a public, unauthenticated detail page at
#   /jobs/{req_id} (redirects to /external/jobs/{req_id} on some tenants) —
#   itself carrying a real JobPosting JSON-LD block — so that's what's
#   stored, not apply_url.
_CLASSIC_SEARCH_URL = "https://{host}/jobs/search"
_CLASSIC_JOB_LINK_RE = re.compile(r'href="(https://[a-zA-Z0-9.-]+/jobs/\d+/[^"]*)"')
_CLASSIC_PAGE_SIZE = 50

_JIBE_REDIRECT_RE = re.compile(r"window\.top\.location\.href\s*=\s*'([^']+)'")
_JIBE_API_URL = "https://{host}/api/jobs"
_JIBE_JOB_URL = "https://{host}/jobs/{req_id}"

# Jibe pages sit on the vanity domain, which is only reachable from the
# stub via this redirect; classic tenants have no such script and just
# render the listing directly at the same URL.
def _classic_or_redirect_host(host: str) -> tuple[list[str] | None, str | None]:
    try:
        response = get_with_retry(_CLASSIC_SEARCH_URL.format(host=host), params={"ss": 1, "in_iframe": 1}, timeout=TIMEOUT)
        response.raise_for_status()
    except httpx.HTTPError:
        return None, None
    redirect = _JIBE_REDIRECT_RE.search(response.text)
    if redirect:
        target = redirect.group(1).replace("\\/", "/")
        return None, urlsplit(target).netloc or None
    links = dict.fromkeys(_CLASSIC_JOB_LINK_RE.findall(response.text))
    return list(links), None


def _fetch_classic_jobs(host: str) -> list[str]:
    urls: list[str] = []
    page = 0
    while len(urls) < _ICIMS_MAX_JOBS:
        response = get_with_retry(
            _CLASSIC_SEARCH_URL.format(host=host), params={"ss": 1, "in_iframe": 1, "pr": page}, timeout=TIMEOUT
        )
        response.raise_for_status()
        links = dict.fromkeys(_CLASSIC_JOB_LINK_RE.findall(response.text))
        if not links:
            break
        urls.extend(links)
        if len(links) < _CLASSIC_PAGE_SIZE:
            break
        page += 1
    return urls[:_ICIMS_MAX_JOBS]


def _fetch_jibe_jobs(host: str) -> list[str] | None:
    # Returns None (rather than []) when the host clearly isn't running
    # Jibe at all (verified live: classic tenants' /api/jobs just serves
    # their classic HTML shell, not JSON) — a real Jibe tenant with zero
    # current postings would still return valid JSON with an empty list,
    # which is a legitimate [] result, not None.
    urls: list[str] = []
    page = 1
    while len(urls) < _ICIMS_MAX_JOBS:
        try:
            response = get_with_retry(
                _JIBE_API_URL.format(host=host),
                params={"lang": "en-US", "page": page, "sortBy": "posted_date", "descending": "true", "internal": "false"},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return None if page == 1 else urls[:_ICIMS_MAX_JOBS]
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if jobs is None:
            return None if page == 1 else urls[:_ICIMS_MAX_JOBS]
        if not jobs:
            break
        recent = [
            job["data"]
            for job in jobs
            if isinstance(job, dict) and isinstance(job.get("data"), dict) and is_recent_posting(job["data"].get("posted_date"))
        ]
        urls.extend(
            _JIBE_JOB_URL.format(host=host, req_id=job_data["req_id"]) for job_data in recent if job_data.get("req_id")
        )
        if len(recent) < len(jobs):
            break
        page += 1
    return urls[:_ICIMS_MAX_JOBS]


def _fetch_jobs(host: str) -> list[str]:
    jibe_urls = _fetch_jibe_jobs(host)
    if jibe_urls is not None:
        return jibe_urls

    classic_urls, redirect_host = _classic_or_redirect_host(host)
    if redirect_host:
        jibe_urls = _fetch_jibe_jobs(redirect_host)
        if jibe_urls is not None:
            return jibe_urls
    if classic_urls:
        return _fetch_classic_jobs(host)
    return []


def _match(url: str) -> str | None:
    match = _ICIMS_URL_RE.search(url)
    return match.group(1) if match else None


def _board_key(url: str) -> str | None:
    return urlsplit(url).netloc or None


# Some tenants white-label Jibe/Attract entirely onto their own domain with
# no icims.com host visible anywhere in the URL (careers.ellucian.com,
# careers.mheducation.com, verified live) — only detectable by fetching the
# page and checking for iCIMS/Jibe's own signatures.
_EMBED_SIGNATURES = ("jibecdn.com", ".icims.com")


def _detect_embedded(url: str) -> str | None:
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    if not any(sig in response.text.lower() for sig in _EMBED_SIGNATURES):
        return None
    return urlsplit(str(response.url)).netloc or None


# No to_board_url — the Jibe/Attract half has no shared host to
# canonicalize to (each tenant's real board lives on its own vanity
# domain), so board_url is stored verbatim, as submitted/discovered, same
# reasoning as Oracle Fusion/Clinch/Attrax/Phenom.
ADAPTER = AtsAdapter(
    AtsType.ICIMS,
    match=_match,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
    embedded_match=_detect_embedded,
)
