import json
import re
from datetime import date
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType
from app.services.adapters import base
from app.services.adapters.base import (
    DEFAULT_MAX_JOBS_PER_CRAWL,
    TIMEOUT,
    AtsAdapter,
    ExtractedJobFields,
    ScanResult,
    get_with_retry,
    limit_job_urls,
    post_with_retry,
)
from app.services.adapters.text import html_to_formatted_text

# Cornerstone OnDemand (CSOD) — a multi-tenant ATS at {corp}.csod.com. One
# corp tenant hosts several independent "career sites" (one per country/
# brand, verified live against Cencosud: site 5 = Chile, site 9 =
# Argentina, site 16 = an internship talent pool, ...), each with its own
# numeric careerSiteId and its own job listings — there's no tenant-wide
# job feed, so board_key has to carry both corp and site id, same shape as
# Workday's "company/instance/site".
_CSOD_HOST_RE = re.compile(r"([a-z0-9-]+)\.csod\.com", re.IGNORECASE)
_CSOD_SITE_ID_RE = re.compile(r"/careersite/(\d+)/", re.IGNORECASE)
_CSOD_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_CSOD_PAGE_SIZE = 100
_JOBS_API_URL = "https://us.api.csod.com/rec-job-search/external/jobs"
_JOB_URL = "https://{corp}.csod.com/ux/ats/careersite/{site_id}/home/requisition/{req_id}?c={corp}"

# The career site's own SPA shell server-renders its session context
# (culture, and a short-lived anonymous JWT the search API requires) into
# a plain script tag — no browser needed, a regular GET already returns it
# (verified live). Non-greedy up to the first "};" is safe here: the only
# nested braces are inside string values (a JWT, HTML fragments), none of
# which contain a literal "};" substring.
_CONTEXT_RE = re.compile(r"csod\.context\s*=\s*(\{.*?\});", re.DOTALL)


def _match(url: str) -> str | None:
    host_match = _CSOD_HOST_RE.search(url)
    site_match = _CSOD_SITE_ID_RE.search(url)
    if not host_match or not site_match:
        return None
    return f"{host_match.group(1)}/{site_match.group(1)}"


def _board_url(board_key: str) -> str:
    corp, site_id = board_key.split("/")
    return f"https://{corp}.csod.com/ux/ats/careersite/{site_id}/home?c={corp}"


def _fetch_context(corp: str, site_id: str) -> dict[str, Any] | None:
    response = get_with_retry(_board_url(f"{corp}/{site_id}"), timeout=TIMEOUT)
    response.raise_for_status()
    match = _CONTEXT_RE.search(response.text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _search_requisitions(corp: str, site_id: str, context: dict[str, Any]) -> list[dict[str, Any]]:
    site_id_int = int(site_id)
    headers = {"Authorization": f"Bearer {context['token']}"}
    requisitions: list[dict[str, Any]] = []
    page = 1
    total = None
    while len(requisitions) < _CSOD_MAX_JOBS and (total is None or len(requisitions) < total):
        response = post_with_retry(
            _JOBS_API_URL,
            headers=headers,
            json={
                "careerSiteId": site_id_int,
                "careerSitePageId": site_id_int,
                "pageNumber": page,
                "pageSize": _CSOD_PAGE_SIZE,
                "cultureId": context.get("cultureID"),
                "searchText": "",
                "cultureName": context.get("cultureName"),
                "states": [],
                "countryCodes": [],
                "cities": [],
                "placeID": "",
                "radius": None,
                "postingsWithinDays": None,
                "customFieldCheckboxKeys": [],
                "customFieldDropdowns": [],
                "customFieldRadios": [],
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json().get("data", {})
        total = data.get("totalCount", 0)
        batch = data.get("requisitions", [])
        if not batch:
            break
        requisitions.extend(batch)
        page += 1
    return requisitions[:_CSOD_MAX_JOBS]


def _fetch_jobs(board_key: str) -> list[str]:
    corp, site_id = board_key.split("/")
    context = _fetch_context(corp, site_id)
    if context is None or not context.get("token"):
        raise ValueError(f"Couldn't resolve a CSOD session token for corp={corp!r} site_id={site_id!r}")
    requisitions = _search_requisitions(corp, site_id, context)
    return limit_job_urls(
        _JOB_URL.format(corp=corp, site_id=site_id, req_id=req["requisitionId"])
        for req in requisitions
        if req.get("requisitionId") is not None
    )


def _location_of(loc: dict[str, Any]) -> str | None:
    parts = [loc.get(key) for key in ("city", "state", "country")]
    text = ", ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
    return text or None


def _location_of_requisition(req: dict[str, Any]) -> str | None:
    locations = req.get("locations")
    if not isinstance(locations, list):
        return None
    texts = list(dict.fromkeys(t for t in (_location_of(loc) for loc in locations if isinstance(loc, dict)) if t))
    return "; ".join(texts) or None


# DD/MM/YYYY, unlike every ISO-ish date base.posting_date already handles.
def _posted_at_of(req: dict[str, Any]) -> date | None:
    raw = req.get("postingEffectiveDate")
    if not isinstance(raw, str):
        return None
    try:
        day, month, year = raw.split("/")
        return date(int(year), int(month), int(day))
    except (ValueError, TypeError):
        return None


def extract(url: str, context: dict[str, Any]) -> tuple[ExtractedJobFields | None, dict[str, Any] | None]:
    site_match = _CSOD_SITE_ID_RE.search(url)
    req_id_match = re.search(r"/requisition/(\d+)", url)
    host_match = _CSOD_HOST_RE.search(url)
    if not site_match or not req_id_match or not host_match:
        return None, None
    corp, site_id = host_match.group(1), site_match.group(1)
    for req in _search_requisitions(corp, site_id, context):
        if str(req.get("requisitionId")) == req_id_match.group(1):
            fields = ExtractedJobFields(
                title=req.get("displayJobTitle") if isinstance(req.get("displayJobTitle"), str) else None,
                description=html_to_formatted_text(req.get("externalDescription")),
                location=_location_of_requisition(req),
                posted_at=_posted_at_of(req),
            )
            return fields, req
    return None, None


def scan_job_url(url: str) -> ScanResult | None:
    if _match(url) is None:
        return None
    host = urlsplit(url).netloc
    corp = host.split(".")[0]
    site_id = _CSOD_SITE_ID_RE.search(url).group(1)
    try:
        context = _fetch_context(corp, site_id)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    if context is None or not context.get("token"):
        return ScanResult(success=False, error="Couldn't resolve a CSOD session token.")

    try:
        fields, _ = extract(url, context)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    if fields is None:
        return ScanResult(success=False, error="Requisition not found in this career site's current listings.")

    salary_min, salary_max, salary_currency = base.salary_from_text(fields.description)
    return ScanResult(
        success=True,
        title=fields.title,
        description=fields.description,
        company_name=corp,
        location=fields.location,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=fields.posted_at,
    )


ADAPTER = AtsAdapter(
    AtsType.CSOD,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=_board_url,
    scan_job_url=scan_job_url,
)
