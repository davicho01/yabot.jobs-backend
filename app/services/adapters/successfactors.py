import re
from datetime import date, datetime
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType
from app.services.adapters import base
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, ScanResult, get_with_retry
from app.services.adapters.text import clean_text, html_to_formatted_text

_SUCCESSFACTORS_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_SUCCESSFACTORS_PAGE_SIZE = 25
# SAP SuccessFactors' Career Site Builder ("j2w"/Job-to-Web) is a shared
# multi-tenant ATS, but — like Clinch/Echo Jobs — fully white-labeled onto
# each customer's own domain (careers.firstsource.com, verified live) with
# no ATS host visible in the URL, so it's only reachable via embedded_match.
# All discovery/scanning stays on that same customer domain too — the actual
# SuccessFactors-hosted host (career5.successfactors.eu, regional/sharded
# per tenant) never needs to be resolved.
_SIGNATURE = "successfactors"
_JOB_LINK_RE = re.compile(r'href="(/job/[^"]+/\d+/)"')
# Verified live: some postings wrap the title in <span itemprop="title">,
# others in <h1 itemprop="title"> — matching up to the next "<" (title text
# itself carries no inner tags either way) handles both without caring
# which element wraps it.
_TITLE_RE = re.compile(r'itemprop="title"[^>]*>([^<]*)', re.IGNORECASE)
_DATE_POSTED_RE = re.compile(r'itemprop="datePosted" content="([^"]*)"', re.IGNORECASE)
_ADDRESS_PART_RE = re.compile(
    r'itemprop="(addressLocality|addressRegion|addressCountry|streetAddress)" content="([^"]*)"'
)
# Present on every tenant's job detail page (verified live across five: Lincoln
# Financial, Erie Insurance, Paramount, PACCAR, Farmers Insurance) but never
# extracted — every SuccessFactors-sourced posting in prod has a null
# company_name as a result.
_COMPANY_NAME_RE = re.compile(r'itemprop="hiringOrganization" content="([^"]*)"', re.IGNORECASE)
_DESCRIPTION_RE = re.compile(r'itemprop="description"[^>]*>(.*?)<p class="job-location">', re.IGNORECASE | re.DOTALL)
_JOB_PATH_SIGNATURE = "/job/"


def _fetch_jobs(host: str) -> list[str]:
    urls: list[str] = []
    startrow = 0
    while len(urls) < _SUCCESSFACTORS_MAX_JOBS:
        response = get_with_retry(f"https://{host}/search/", params={"startrow": startrow}, timeout=TIMEOUT)
        response.raise_for_status()
        paths = dict.fromkeys(_JOB_LINK_RE.findall(response.text))
        if not paths:
            break
        urls.extend(f"https://{host}{path}" for path in paths)
        startrow += _SUCCESSFACTORS_PAGE_SIZE
    return urls[:_SUCCESSFACTORS_MAX_JOBS]


def _detect_embedded(url: str) -> str | None:
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    host = urlsplit(str(response.url)).netloc
    if not host:
        return None
    if _SIGNATURE in response.text.lower():
        return host
    # Mirrors clinch.py/echo_jobs.py: some tenants' marketing/home page might
    # not carry the signature even though their /search/ listing does.
    try:
        return host if _fetch_jobs(host) else None
    except httpx.HTTPError:
        return None


def _board_key(url: str) -> str | None:
    return urlsplit(url).netloc or None


def _location_of(html: str) -> str | None:
    parts = dict(_ADDRESS_PART_RE.findall(html))
    bits = (parts.get("addressLocality"), parts.get("addressRegion"), parts.get("addressCountry"))
    location = ", ".join(p.strip() for p in bits if p and p.strip())
    if location:
        return location
    # Some tenants leave locality/region/country empty on certain postings
    # (nationwide/remote-eligible roles, verified live on Farmers Insurance)
    # and put the only location hint in streetAddress instead (e.g. "US").
    street = parts.get("streetAddress")
    return street.strip() if street and street.strip() else None


def _company_name_of(html: str) -> str | None:
    match = _COMPANY_NAME_RE.search(html)
    return clean_text(match.group(1)) if match else None


def _posted_at_of(html: str) -> date | None:
    match = _DATE_POSTED_RE.search(html)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%a %b %d %H:%M:%S %Z %Y").date()
    except ValueError:
        return None


def scan_job_url(url: str) -> ScanResult | None:
    if _JOB_PATH_SIGNATURE not in urlsplit(url).path:
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    html = page.text
    title_match = _TITLE_RE.search(html)
    if title_match is None:
        # A bare "successfactors" text-substring check here false-positived
        # on a real posting (Cargill, TalentBrew-templated) whose own
        # description just happened to *mention* SuccessFactors — the role
        # was for administering SF software, and its apply link pointed at
        # a real SF instance, but the listing page itself wasn't SF's own
        # template. itemprop="title" is the actual structural marker this
        # extractor depends on, so require it directly rather than a
        # substring that can appear in any page's prose/apply-link/keywords.
        return None
    description_match = _DESCRIPTION_RE.search(html)
    return ScanResult(
        success=True,
        title=clean_text(title_match.group(1)) if title_match else base.fallback_title(html),
        description=html_to_formatted_text(description_match.group(1)) if description_match else None,
        company_name=_company_name_of(html),
        location=_location_of(html),
        posted_at=_posted_at_of(html),
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


# No to_board_url — SuccessFactors' actual ATS host is a regional/sharded
# per-tenant subdomain (career5.successfactors.eu, verified live) with no
# single canonical shared host to templatize, and every discovery/scan path
# above already stays on the customer's own domain anyway — board_url is
# stored verbatim, same reasoning as Oracle Fusion/Clinch.
ADAPTER = AtsAdapter(
    AtsType.SUCCESSFACTORS,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
    embedded_match=_detect_embedded,
    scan_job_url=scan_job_url,
)
