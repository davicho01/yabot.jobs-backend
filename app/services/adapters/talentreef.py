import re
from datetime import date
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType, EmploymentType
from app.services.adapters.base import (
    RECENT_WINDOW_DAYS,
    TIMEOUT,
    AtsAdapter,
    ExtractedJobFields,
    ScanResult,
    get_with_retry,
    limit_job_urls,
    posting_date,
)

_SIGNATURE = "talentreef"
# Every TalentReef-hosted vanity career site (e.g. jackintheboxjobs.com)
# embeds its numeric tenant id in its own marketing-asset URLs (og:image,
# header/background images — verified live) even though nothing in the
# page otherwise reveals it; no dedicated "who am I" endpoint found.
_CLIENT_ID_RE = re.compile(r"marketing-assets\.jobappnetwork\.com/(\d+)/")
_BRANDS_URL = "https://prod-kong.internal.talentreef.com/clients/{client_id}/recruit/public/brands"
# A public, unauthenticated Elasticsearch proxy shared by every TalentReef
# tenant — no client scoping in the URL itself, only in the query body's
# own brandId filter (verified live: an unfiltered match_all query returns
# >190k postings across unrelated brands like Dunkin). The site's own
# search widget filters by a long, tenant-resolved list of clientIds
# instead of brandId, but filtering directly on brandId (from the /brands
# endpoint above) returns the same population without needing to know that
# resolution mechanism.
_SEARCH_URL = "https://prod-kong.internal.talentreef.com/apply/proxy-es/search-en-us/posting/_search"
_PAGE_SIZE = 200
_JOB_ID_RE = re.compile(r"-(\d+)\.html$")
# Only "fullTime" confirmed against real postings; anything else falls back
# to UNKNOWN rather than guessing at the full label set.
_EMPLOYMENT_TYPE_MAP = {
    "fullTime": EmploymentType.FULL_TIME,
    "partTime": EmploymentType.PART_TIME,
    "temporary": EmploymentType.TEMPORARY,
    "intern": EmploymentType.INTERNSHIP,
}


def _client_id(html: str) -> str | None:
    match = _CLIENT_ID_RE.search(html)
    return match.group(1) if match else None


def _brand_ids(client_id: str) -> list[str]:
    response = get_with_retry(_BRANDS_URL.format(client_id=client_id), timeout=TIMEOUT)
    response.raise_for_status()
    brands = response.json()
    return [b["id"] for b in brands if isinstance(b, dict) and b.get("id")]


def _search(brand_ids: list[str], *, from_: int, extra_filters: list[dict] | None = None) -> dict:
    filters = [{"terms": {"brandId": brand_ids}}, *(extra_filters or [])]
    body = {
        "from": from_,
        "size": _PAGE_SIZE,
        "sort": [{"createdDate": {"order": "desc"}}],
        "_source": ["jobId", "url"],
        "query": {"bool": {"filter": filters}},
    }
    response = httpx.post(_SEARCH_URL, json=body, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def _fetch_jobs(host: str) -> list[str]:
    homepage = get_with_retry(f"https://{host}", timeout=TIMEOUT, follow_redirects=True)
    homepage.raise_for_status()
    client_id = _client_id(homepage.text)
    if client_id is None:
        raise ValueError(f"Couldn't resolve a TalentReef client id for host={host!r}")
    brand_ids = _brand_ids(client_id)
    if not brand_ids:
        return []

    # High-volume boards (Jack in the Box alone runs ~7k open reqs across
    # its franchisee network) — filter server-side to just-posted-recently
    # rather than paginating the entire board every crawl, same reasoning
    # as Workday/Amazon/Oracle Fusion (see base.RECENT_WINDOW_DAYS).
    date_filter = [{"range": {"createdDate": {"gte": f"now-{RECENT_WINDOW_DAYS - 1}d/d"}}}]
    urls: list[str] = []
    from_ = 0
    total = None
    while total is None or from_ < total:
        data = _search(brand_ids, from_=from_, extra_filters=date_filter)
        hits = data.get("hits", {})
        total = hits.get("total", 0)
        batch = hits.get("hits", [])
        if not batch:
            break
        for hit in batch:
            rel_url = hit.get("_source", {}).get("url")
            if rel_url:
                urls.append(f"https://{host}{rel_url}")
        from_ += len(batch)
    return limit_job_urls(urls)


def _detect_embedded(url: str) -> str | None:
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    html = response.text
    if _SIGNATURE not in html.lower() or _client_id(html) is None:
        return None
    return urlsplit(str(response.url)).netloc or None


def _board_key(url: str) -> str | None:
    return urlsplit(url).netloc or None


def _job_data(job_id: str) -> dict[str, Any] | None:
    body = {"from": 0, "size": 1, "query": {"bool": {"filter": [{"term": {"jobId": int(job_id)}}]}}}
    response = httpx.post(_SEARCH_URL, json=body, timeout=TIMEOUT)
    response.raise_for_status()
    hits = response.json().get("hits", {}).get("hits", [])
    return hits[0]["_source"] if hits else None


def _location(job_data: dict[str, Any]) -> str | None:
    address = job_data.get("address") or {}
    city = address.get("city")
    state = address.get("stateOrProvince")
    parts = [p for p in (city, state) if p]
    return ", ".join(parts) if parts else None


def extract(job_data: dict[str, Any]) -> ExtractedJobFields:
    return ExtractedJobFields(
        title=job_data.get("title") if isinstance(job_data.get("title"), str) else None,
        description=job_data.get("description") if isinstance(job_data.get("description"), str) else None,
        company_name=job_data.get("clientName") if isinstance(job_data.get("clientName"), str) else None,
        location=_location(job_data),
        employment_type=_EMPLOYMENT_TYPE_MAP.get(job_data.get("contractType"), EmploymentType.UNKNOWN),
        posted_at=posting_date(job_data.get("createdDate")),
    )


def scan_job_url(url: str) -> ScanResult | None:
    # Direct fetches of the job page itself 403/blank (verified live: the
    # vanity domain's /apply/... route is CloudFront/S3-backed and doesn't
    # serve those paths outside the SPA's own client-side router) — so
    # extraction goes straight through the same public ES endpoint
    # fetch_jobs uses, keyed off the jobId already embedded in the URL,
    # same approach as eightfold.py's position_details lookup.
    match = _JOB_ID_RE.search(url)
    if match is None:
        return None
    try:
        job_data = _job_data(match.group(1))
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    if job_data is None:
        return None

    fields = extract(job_data)
    return ScanResult(
        success=True,
        title=fields.title,
        description=fields.description,
        company_name=fields.company_name,
        location=fields.location,
        employment_type=fields.employment_type,
        posted_at=fields.posted_at,
    )


# No shared host to canonicalize to — board_url is the company's own
# vanity domain, stored verbatim, same reasoning as Clinch/Eightfold.
ADAPTER = AtsAdapter(
    AtsType.TALENTREEF,
    board_key=_board_key,
    fetch_jobs=_fetch_jobs,
    embedded_match=_detect_embedded,
    scan_job_url=scan_job_url,
)
