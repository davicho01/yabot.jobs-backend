import re

import httpx

from app.models.enums import AtsType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, ScanResult, get_with_retry
from app.services.adapters.text import clean_text, html_to_formatted_text

_PAYCOR_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_CAREER_HOME_URL = "https://recruitingbypaycor.com/career/CareerHome.action"
_JOB_URL = "https://recruitingbypaycor.com/career/JobIntroduction.action"
# Paycor Recruiting (formerly Newton Software, still branded "gnewton" in its
# own embed script/element ids) is a shared multi-tenant ATS white-labeled
# onto each customer's own careers page via an iframe-building embed script
# — the tenant's clientId never appears in the wrapper page's own URL, only
# inside that script's src (verified live against promevo.com/careers). A
# job or CareerHome URL submitted directly (recruitingbypaycor.com itself)
# does carry clientId as a query param, so that tier is a pure string match;
# only the white-label wrapper page needs an actual fetch.
_URL_CLIENT_ID_RE = re.compile(
    r"recruitingbypaycor\.com/career/[A-Za-z]+\.action\?[^\"'\s]*\bclientId=([a-f0-9]+)", re.IGNORECASE
)
_EMBED_SCRIPT_RE = re.compile(r"recruitingbypaycor\.com/career/iframe\.action\?clientId=([a-f0-9]+)", re.IGNORECASE)
_JOB_LINK_RE = re.compile(r"JobIntroduction\.action\?clientId=([a-f0-9]+)&id=([a-f0-9]+)", re.IGNORECASE)
_POSITION_RE = re.compile(r"<b>Position:</b>&nbsp;(.*?)</td>", re.IGNORECASE | re.DOTALL)
_LOCATION_RE = re.compile(r'id="gnewtonJobLocationInfo"[^>]*>(.*?)</td>', re.IGNORECASE | re.DOTALL)
_DESCRIPTION_RE = re.compile(r'id="gnewtonJobDescriptionText"[^>]*>(.*?)</td>', re.IGNORECASE | re.DOTALL)
_WORKPLACE_TYPE_WORDS = {
    "remote": WorkplaceType.REMOTE,
    "hybrid": WorkplaceType.HYBRID,
    "onsite": WorkplaceType.ONSITE,
    "on-site": WorkplaceType.ONSITE,
    "on site": WorkplaceType.ONSITE,
}


def _match(url: str) -> str | None:
    match = _URL_CLIENT_ID_RE.search(url)
    return match.group(1) if match else None


def _detect_embedded(url: str) -> str | None:
    client_id = _match(url)
    if client_id:
        return client_id
    # Some wrapper pages (verified live: promevo.com) 404 a plain httpx
    # request even with base.fetch_html's own bot UA, but serve the same
    # embed script fine to a real browser — its browser-render fallback
    # handles that the same way every scan_job_url fetch does.
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError:
        return None
    match = _EMBED_SCRIPT_RE.search(page.text)
    return match.group(1) if match else None


def _fetch_jobs(client_id: str) -> list[str]:
    response = get_with_retry(_CAREER_HOME_URL, params={"clientId": client_id}, timeout=TIMEOUT)
    response.raise_for_status()
    ids = dict.fromkeys(job_id for _, job_id in _JOB_LINK_RE.findall(response.text))
    urls = [f"{_JOB_URL}?clientId={client_id}&id={job_id}" for job_id in ids]
    return urls[:_PAYCOR_MAX_JOBS]


def _workplace_type_of(location: str | None) -> str:
    return _WORKPLACE_TYPE_WORDS.get((location or "").strip().lower(), WorkplaceType.UNKNOWN)


def scan_job_url(url: str) -> ScanResult | None:
    if not _URL_CLIENT_ID_RE.search(url) or "JobIntroduction.action" not in url:
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    html = page.text
    position_match = _POSITION_RE.search(html)
    location_match = _LOCATION_RE.search(html)
    description_match = _DESCRIPTION_RE.search(html)
    location = clean_text(location_match.group(1)) if location_match else None

    return ScanResult(
        success=True,
        title=clean_text(position_match.group(1)) if position_match else base.fallback_title(html),
        description=html_to_formatted_text(description_match.group(1)) if description_match else None,
        company_name=base.og_site_name(html),
        location=location,
        workplace_type=_workplace_type_of(location),
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.PAYCOR_RECRUITING,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda client_id: f"{_CAREER_HOME_URL}?clientId={client_id}",
    embedded_match=_detect_embedded,
    scan_job_url=scan_job_url,
)
