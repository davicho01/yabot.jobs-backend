import re
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import (
    DEFAULT_MAX_JOBS_PER_CRAWL,
    TIMEOUT,
    AtsAdapter,
    ExtractedJobFields,
    ScanResult,
    get_with_retry,
)
from app.services.adapters.text import html_to_formatted_text

_EIGHTFOLD_SEARCH_PAGE_SIZE = 20
_EIGHTFOLD_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_ROBOTS_SITEMAP_RE = re.compile(r"^Sitemap:\s*(\S+)", re.MULTILINE | re.IGNORECASE)
# Eightfold's white-label career sites (e.g. jobs.twilio.com,
# explore.jobs.netflix.net) all serve from a `/careers/job/{numeric_id}`
# path and share a distinctive, stable string in their page HTML even
# though the listing itself renders via JS — verified against two live
# instances. Unlike Greenhouse/Ashby's embeds, this one carries no board
# key in the URL *or* the page at all: the API just wants the tenant's own
# "domain" value, usually (not always — Netflix's host ends .net but its
# domain value would be .com) the requesting host's registrable domain, so
# the same guess-and-verify approach as Ashby applies, keyed off the known
# job id from the URL path instead of a query param.
_EIGHTFOLD_JOB_URL_RE = re.compile(r"/careers/job/(\d+)")
_EIGHTFOLD_SIGNATURE = "eightfold.ai/privacy-policy"


def _domain_hint(url: str) -> str | None:
    # Eightfold's own listing API emits job URLs like
    # https://paypal.eightfold.ai/careers/job/123-title?domain=paypal.com —
    # the tenant's real domain rides along in the query string, and it's the
    # only place it appears for tenants hosted on <tenant>.eightfold.ai
    # (whose host says nothing about the tenant's own domain).
    values = parse_qs(urlsplit(url).query).get("domain")
    return values[0] if values else None


def _candidate_domains(host: str, hint: str | None = None) -> list[str]:
    """Eightfold's "domain" tenant identifier is usually the requesting
    host's registrable domain, but the career site itself often lives on a
    subdomain (jobs.twilio.com's tenant domain is twilio.com) — try the host
    as-is, then progressively strip leading subdomain labels.

    Tenants hosted on <tenant>.eightfold.ai itself are the exception: the
    host and its parents (paypal.eightfold.ai, eightfold.ai) never match the
    tenant's own domain (paypal.com), so an explicit hint from the job URL's
    ?domain= param goes first, and <tenant>.com is tried last as a guess for
    when no hint is available (a bare board URL, or a hand-copied job link).
    Every caller verifies a candidate against the API before trusting it, so
    a wrong guess costs a request, nothing more.
    """
    labels = host.lower().split(".")
    candidates = [hint.lower()] if hint else []
    candidates.append(host.lower())
    for strip in (1, 2):
        if len(labels) > strip + 1:
            candidates.append(".".join(labels[strip:]))
    if len(labels) == 3 and ".".join(labels[1:]) == "eightfold.ai":
        candidates.append(f"{labels[0]}.com")
    return list(dict.fromkeys(candidates))  # dedupe, keep order


def _domain_is_valid(host: str, domain: str) -> bool:
    # A wrong guess usually fails outright (e.g. Netflix's 403 "PCSX is not
    # enabled for this user"), but some hosts that aren't Eightfold at all
    # happen to answer this same path with a 200 anyway — verified against
    # careers.freedommortgage.com (a Phenom site), which returns
    # {"status":"failure","data":null,"errorMsg":"Tenant not identified"}.
    # So success has to be judged from the body's shape, not just the HTTP
    # status: a genuinely valid tenant, even with zero open roles, still
    # comes back with a "data" dict rather than null.
    try:
        response = get_with_retry(
            f"https://{host}/api/pcsx/search", params={"domain": domain, "start": 0, "num": 1}, timeout=TIMEOUT
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError:
        return False
    return isinstance(body, dict) and isinstance(body.get("data"), dict)


def _sitemap_job_urls(host: str) -> list[str]:
    # Fallback for tenants that have disabled /api/pcsx/search entirely
    # (Netflix: 403 "PCSX is not enabled for this user" on every domain
    # guess). Some Eightfold instances still publish a sitemap — Netflix's
    # robots.txt carries a "Sitemap:" line pointing at a sitemap_index.xml
    # that's already domain-qualified (no need to guess the tenant "domain"
    # value at all), listing a jobs sitemap.xml (job postings, path
    # /careers/job/...) alongside a sitemap_cat.xml (facet/category pages,
    # no job content) — verified against a live instance. Not every
    # Eightfold tenant has this (Twilio's robots.txt has no Sitemap: line),
    # but that's fine since this is only reached once pcsx has already
    # failed outright.
    robots = httpx.get(f"https://{host}/robots.txt", timeout=TIMEOUT)
    robots.raise_for_status()
    match = _ROBOTS_SITEMAP_RE.search(robots.text)
    if not match:
        return []
    index = httpx.get(match.group(1), timeout=TIMEOUT)
    index.raise_for_status()
    index_root = ElementTree.fromstring(index.content)

    urls: list[str] = []
    for loc in index_root.findall(".//sm:sitemap/sm:loc", _SITEMAP_NS):
        if not loc.text or "_cat" in loc.text:
            continue
        sitemap = httpx.get(loc.text, timeout=TIMEOUT)
        sitemap.raise_for_status()
        sitemap_root = ElementTree.fromstring(sitemap.content)
        urls.extend(
            u.text
            for u in sitemap_root.findall(".//sm:url/sm:loc", _SITEMAP_NS)
            if u.text and urlsplit(u.text).path.startswith("/careers/job/")
        )
        if len(urls) >= DEFAULT_MAX_JOBS_PER_CRAWL:
            break
    return urls[:DEFAULT_MAX_JOBS_PER_CRAWL]


def _fetch_jobs(host: str) -> list[str]:
    # Free, public API — no key required, but only when the company hasn't
    # disabled it on their instance (Netflix returns 403 "PCSX is not
    # enabled for this user" on the very same endpoint that works fine for
    # Twilio). The "domain" value the API expects to identify the tenant
    # isn't derivable from the URL alone (see _candidate_domains), so it's
    # cheaply re-resolved on every crawl via the same candidate-guessing
    # embedded detection uses, rather than cached anywhere.
    domain = next((d for d in _candidate_domains(host) if _domain_is_valid(host, d)), None)
    if domain is None:
        sitemap_urls = _sitemap_job_urls(host)
        if sitemap_urls:
            return sitemap_urls[:_EIGHTFOLD_MAX_JOBS]
        raise ValueError(f"Couldn't resolve an Eightfold tenant domain for host={host!r}")

    urls: list[str] = []
    start = 0
    total = None
    # The "num" param above is a request, not a promise — verified against
    # a live instance ignoring num=20 and always returning 10 per page
    # regardless — so pagination advances by however many actually came
    # back each time, and stops using the response's own "count" rather
    # than assuming a fixed page size.
    while len(urls) < _EIGHTFOLD_MAX_JOBS and (total is None or start < total):
        response = get_with_retry(
            f"https://{host}/api/pcsx/search",
            params={"domain": domain, "start": start, "num": _EIGHTFOLD_SEARCH_PAGE_SIZE},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json().get("data", {})
        positions = data.get("positions", [])
        total = data.get("count", 0)
        if not positions:
            break
        urls.extend(f"https://{host}{p['positionUrl']}" for p in positions if p.get("positionUrl"))
        start += len(positions)

    return urls[:_EIGHTFOLD_MAX_JOBS]


def _detect_embedded(url: str) -> str | None:
    job_match = _EIGHTFOLD_JOB_URL_RE.search(url)
    host = urlsplit(url).netloc
    if not job_match:
        # No job id in the URL — e.g. re-confirming a CrawlSource's bare
        # board_url (the admin PATCH flow) rather than detecting from a
        # freshly submitted job link. Same two-tier fallback as
        # clinch.py's _detect_embedded: check the page for the signature
        # string, then confirm fetch_jobs actually works for the host.
        # The signature alone isn't enough for tenants like Netflix, whose
        # white-labeled careers page doesn't link to eightfold.ai's own
        # privacy policy at all (custom-branded instead) — so a missing
        # signature must fall through to the fetch_jobs check rather than
        # bail out, same as clinch.py does.
        if not host:
            return None
        try:
            response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        if _EIGHTFOLD_SIGNATURE in response.text:
            return host
        try:
            return host if _fetch_jobs(host) else None
        except (httpx.HTTPError, ValueError, ElementTree.ParseError):
            return None

    job_id = job_match.group(1)
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    if _EIGHTFOLD_SIGNATURE not in response.text:
        return None

    for domain in _candidate_domains(host, _domain_hint(url)):
        try:
            detail = get_with_retry(
                f"https://{host}/api/pcsx/position_details",
                params={"position_id": job_id, "domain": domain},
                timeout=TIMEOUT,
            )
        except httpx.HTTPError:
            continue
        if detail.status_code != 200:
            continue
        data = detail.json().get("data")
        if isinstance(data, dict) and str(data.get("id")) == job_id:
            return f"{host}/{domain}"
    return None


def _board_key(url: str) -> str | None:
    return urlsplit(url).netloc or None


def _board_url(board_key: str) -> str:
    host = board_key.split("/")[0]
    return f"https://{host}"


# The same free public API _fetch_jobs uses for listing also serves one
# job's full record directly — no board token to already know, just the
# requesting host's own "domain" tenant identifier (see _candidate_domains
# above), keyed off the job id already sitting in the URL.
def _fetch_job_data(url: str, html: str) -> dict[str, Any] | None:
    job_match = _EIGHTFOLD_JOB_URL_RE.search(url)
    if job_match is None or _EIGHTFOLD_SIGNATURE not in html:
        return None
    job_id = job_match.group(1)
    host = urlsplit(url).netloc
    for domain in _candidate_domains(host, _domain_hint(url)):
        try:
            response = httpx.get(
                f"https://{host}/api/pcsx/position_details",
                params={"position_id": job_id, "domain": domain},
                timeout=10.0,
            )
        except httpx.HTTPError:
            continue
        if response.status_code != 200:
            continue
        data = response.json().get("data")
        if isinstance(data, dict) and str(data.get("id")) == job_id:
            return data
    return None


def _posted_at_of(job_data: dict[str, Any]) -> date | None:
    creation_ts = job_data.get("creationTs")
    if not isinstance(creation_ts, (int, float)) or not creation_ts:
        return None
    try:
        return date.fromtimestamp(creation_ts)
    except (ValueError, OSError):
        return None


def extract(url: str, html: str) -> ExtractedJobFields | None:
    job_data = _fetch_job_data(url, html)
    if job_data is None:
        return None
    location = job_data.get("location")
    return ExtractedJobFields(
        title=job_data.get("name") if isinstance(job_data.get("name"), str) else None,
        description=html_to_formatted_text(job_data.get("jobDescription")),
        location=location if isinstance(location, str) else None,
        posted_at=_posted_at_of(job_data),
    )


def scan_job_url(url: str) -> ScanResult | None:
    # Cheap, URL-only gate: Eightfold has no static host shape (any
    # white-labeled domain can host one), so this only checks the job-id
    # path shape — confirmed for real below via _EIGHTFOLD_SIGNATURE once
    # the page is actually fetched, same two-tier check _fetch_job_data
    # itself does.
    if not _EIGHTFOLD_JOB_URL_RE.search(url):
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    html = page.text
    if _EIGHTFOLD_SIGNATURE not in html:
        return None  # URL path shape was a coincidence; not actually Eightfold.

    fields = extract(url, html)
    description = (
        (fields.description if fields else None) or base.fallback_description(html) or base.og_description(html)
    )
    og_description_raw = base.og_description_raw(html)
    _, workplace_type = (
        base.parse_og_description(og_description_raw) if og_description_raw else (None, WorkplaceType.UNKNOWN)
    )
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=(fields.title if fields else None) or base.og_title(html) or base.fallback_title(html),
        description=description,
        company_name=base.og_site_name(html),
        location=fields.location if fields else None,
        workplace_type=workplace_type,
        employment_type=EmploymentType.UNKNOWN,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=fields.posted_at if fields else None,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


# Eightfold has no static URL shape either (match=None) — same as Clinch,
# only reachable via embedded_match. Its embedded_match key carries both
# host and domain (needed to name the CrawlSource row), but board_key for
# listing only needs the host — _fetch_jobs re-resolves domain fresh every
# time anyway (see its docstring above), so board_key below is a trivial
# host extraction rather than reusing embedded_match's shape. to_board_url
# drops the domain half and keeps only the host, since that's the
# company's own career-site host — there's no separate ATS-hosted page to
# point at the way there is for every templated adapter.
ADAPTER = AtsAdapter(
    AtsType.EIGHTFOLD,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
    to_board_url=_board_url,
    embedded_match=_detect_embedded,
    scan_job_url=scan_job_url,
)
