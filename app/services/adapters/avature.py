import concurrent.futures
import re
from urllib.parse import urlsplit

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
from app.services.adapters.text import clean_text, html_to_formatted_text
from app.services.browser_fetch import fetch_rendered_page

_AVATURE_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_AVATURE_URL_RE = re.compile(r"([a-zA-Z0-9-]+\.avature\.net)", re.IGNORECASE)
# Most tenants link job details by slug/id path segment
# (/careers/JobDetail/Some-Title/12345); some (verified live:
# careers.ibm.com) instead link by a bare ?jobId= query param
# (/careers/JobDetail?jobId=12345) on the listing page itself - the
# slug-path form still shows up as the *browser's* URL once a query-param
# link is opened (client-side routing rewrites it for the pretty/shareable
# address), which is why an already-`active` tenant's stored job URLs can
# look path-based even though discovery here needs to recognize both shapes.
_JOB_LINK_RE = re.compile(r'href="(https?://[^"]+/careers/JobDetail(?:/[^"?]+|\?jobId=\d+))', re.IGNORECASE)
_JOB_DETAIL_URL_RE = re.compile(r"/careers/JobDetail(?:/|\?jobId=)", re.IGNORECASE)
_DEFAULT_PAGE_SIZE = 6  # observed tenant-configured default (verified: Synopsys)
# Some tenants white-label Avature entirely onto their own domain with no
# *.avature.net hop anywhere in the page (verified live: careers.lululemon.com)
# — _AVATURE_URL_RE never matches, static or embedded. The page still carries
# a set of `<meta name="avature.portal...">` tags Avature itself injects
# (portal.id/name/urlPath), which is otherwise-unused text unlikely to
# collide with another platform, and the existing /careers/JobDetail/
# path + SearchJobs pagination works unmodified on the company's own host.
_AVATURE_META_SIGNATURE = 'name="avature.portal'

# Avature's own schema.org JobPosting JSON-LD only carries the page's first
# content section (verified live: delta.avature.net) - "description" cuts
# off right after the Overview/Responsibilities section, silently dropping
# Benefits, Minimum Qualifications, and Preferred Qualifications entirely,
# and jobLocation's address is emitted empty (addressLocality: ""). The real
# content for both lives only in the rendered page's own template, not in
# any structured field: each section is a separate
# <article class="article--details"> under .description-ajax, with an
# <h2-4 class="article__header__text__title"> heading, and the location
# sits in a `<i class="fa fa-globe">` + `<strong>` pair in the page header.
# Parsed from the raw HTML by tag shape (verified live) rather than a full
# HTML parser, consistent with every other adapter in this file.
_AVATURE_ARTICLE_RE = re.compile(r"<article[^>]*\barticle--details\b[^>]*>(.*?)</article>", re.IGNORECASE | re.DOTALL)
_AVATURE_HEADING_RE = re.compile(r"<h[2-4][^>]*>(.*?)</h[2-4]>", re.IGNORECASE | re.DOTALL)
_AVATURE_LOCATION_RE = re.compile(
    r'<i[^>]*\bfa-globe\b[^>]*>.*?<strong>(.*?)</strong>', re.IGNORECASE | re.DOTALL
)

# Other tenants (verified live: careers.lululemon.com) skip the globe-icon
# header entirely and instead put location in the first article--details
# block (the same heading-less one extract() already skips for description
# purposes) as a set of labelled fields: `<span data-map="item-title">
# <strong>Location:</strong></span><span data-map="item-value">Australia
# </span>`, alongside sibling fields for State/Province/City and City. Build
# "City, State, Country" from whichever of those three labels are present,
# same comma-joined single-place shape as everywhere else in this codebase.
_AVATURE_FIELD_RE = re.compile(
    r'data-map="item-title"[^>]*>\s*<strong>\s*(.*?)\s*</strong>.*?data-map="item-value"[^>]*>(.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)
_AVATURE_LOCATION_FIELD_LABELS = ("City", "State/Province/City", "Location")


def _location_from_fields(html: str) -> str | None:
    fields: dict[str, str] = {}
    for raw_label, raw_value in _AVATURE_FIELD_RE.findall(html):
        label = clean_text(raw_label).rstrip(":")
        value = clean_text(raw_value)
        if label and value:
            fields.setdefault(label, value)
    parts = [fields[label] for label in _AVATURE_LOCATION_FIELD_LABELS if fields.get(label)]
    deduped = list(dict.fromkeys(parts))
    return ", ".join(deduped) if deduped else None


# Neither JSON-LD hiringOrganization nor og:site_name are present at all on
# this tenant's pages (verified live: careers.lululemon.com) - the only
# company-name signal left is the plain `<h1>` Avature renders inside its
# own header__logo link on every page (board and job-detail alike), shared
# template markup rather than tenant-specific styling.
_AVATURE_HEADER_LOGO_RE = re.compile(r'header__logo[^>]*>\s*<h1>\s*(.*?)\s*</h1>', re.IGNORECASE | re.DOTALL)


def _company_name_from_header(html: str) -> str | None:
    match = _AVATURE_HEADER_LOGO_RE.search(html)
    return clean_text(match.group(1)) if match else None


# Some tenants (verified live: careers.lululemon.com, from a residential/
# dev network) front every path with a WAF that hangs a plain httpx request
# outright for any non-browser-looking User-Agent, including our own bot UA
# (app.services.adapters.base's "YabotJobsBot/1.0") - not a fast reject like
# delta.avature.net's empty 202 - but responds instantly with the real
# content for a genuine browser UA. Sending one here avoids paying for a
# browser render (and the timeout beforehand) on every single fetch for
# tenants like this one - the render fallback stays in place below for
# tenants (like delta) that block regardless of UA.
_BROWSER_LIKE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# The UA fix above only helps once a connection actually gets made - from
# our GCP-hosted services specifically (verified live: careers.lululemon.com
# hung the *entire* request past Cloud Run's own 300s platform ceiling, with
# the browser-render fallback below never even invoked), the same request
# that returns in under a second from a residential IP never completes at
# all. That's consistent with a WAF silently black-holing traffic from
# recognized cloud/datacenter ASNs, but could equally be Python's
# socket.create_connection() - which get_with_retry ultimately calls -
# blocking on DNS resolution before TIMEOUT's socket-level deadline ever
# applies; either way, get_with_retry's own `timeout=TIMEOUT` isn't a
# reliable bound in that environment. Running the call in a worker thread
# and enforcing a hard wall-clock .result(timeout=...) bounds it regardless
# of which layer is actually stuck. Python can't forcibly kill a blocked
# thread, so a genuinely hung call leaks its thread for the life of the
# container rather than the life of this one request - an acceptable
# trade-off for a rare, tenant-specific pathological case, versus tying up
# the whole request (and, unbounded, the container) for good.
_HARD_TIMEOUT_BUFFER = 5.0


def _get_with_hard_timeout(url: str, *, headers: dict[str, str]) -> httpx.Response:
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(get_with_retry, url, timeout=TIMEOUT, follow_redirects=True, headers=headers)
    try:
        return future.result(timeout=TIMEOUT + _HARD_TIMEOUT_BUFFER)
    finally:
        executor.shutdown(wait=False)


def _match(url: str) -> str | None:
    match = _AVATURE_URL_RE.search(url)
    return match.group(1) if match else None


def _detect_embedded(url: str) -> str | None:
    html = _fetch_page_html(url)
    if html is None or _AVATURE_META_SIGNATURE not in html:
        return None
    return urlsplit(url).netloc or None


def _board_key(url: str) -> str | None:
    return urlsplit(url).netloc or None


def _fetch_page_html(url: str, *, wait_for_selector: str | None = None) -> str | None:
    try:
        response = _get_with_hard_timeout(url, headers=_BROWSER_LIKE_HEADERS)
        response.raise_for_status()
        if response.text.strip():
            return response.text
    except (httpx.HTTPError, concurrent.futures.TimeoutError):
        pass
    # Some tenants (verified live: delta.avature.net) front every path —
    # including the plain /careers listing and even the RSS feed endpoint,
    # not just some deeper page — with an AWS WAF JS challenge that returns
    # an empty 202 to any plain httpx request, regardless of User-Agent or
    # cookies. Unlike every other adapter in this codebase, a real browser
    # render is the only way to get past it at all here, not just a
    # nicer-to-have fallback for an occasional blocked page (verified: other
    # tenants like synopsys.avature.net need no browser at all).
    rendered = fetch_rendered_page(url, wait_for_selector=wait_for_selector)
    return rendered.html if rendered else None


def _fetch_job_detail_html(url: str) -> str | None:
    # A job-detail page's own content (the location header + every
    # description section) renders in after the initial page load, on its
    # own timeline distinct from the rest of the page - verified live on
    # delta.avature.net: postings with an extra "Internal Movement
    # Eligibility" banner (frontline/operational roles - Ticket/Gate Agent,
    # etc.) consistently came back with a real description but a *missing*
    # location, while postings without that banner (corporate roles)
    # consistently got both - a snapshot-timing race, not a markup
    # difference, since the same globe-icon + <strong> structure is present
    # in both once fully rendered. wait_for_selector blocks the render until
    # that content genuinely exists, closing the race - only for job-detail
    # fetches, since .description-ajax never appears on the board root or
    # SearchJobs listing pages _fetch_page_html also serves.
    return _fetch_page_html(url, wait_for_selector=".description-ajax article")


def _resolve_careers_url(host: str) -> str | None:
    base_url = f"https://{host}/careers"
    try:
        response = _get_with_hard_timeout(base_url, headers=_BROWSER_LIKE_HEADERS)
        response.raise_for_status()
        if response.text.strip():
            return str(response.url).split("?")[0].rstrip("/")
    except (httpx.HTTPError, concurrent.futures.TimeoutError):
        pass
    rendered = fetch_rendered_page(base_url)
    if rendered is None:
        return None
    return rendered.url.split("?")[0].rstrip("/")


def _fetch_jobs(host: str) -> list[str]:
    careers_url = _resolve_careers_url(host)
    if careers_url is None:
        return []

    urls: list[str] = []
    offset = 0
    page_size = _DEFAULT_PAGE_SIZE
    while len(urls) < _AVATURE_MAX_JOBS:
        page_url = f"{careers_url}/SearchJobs/?jobRecordsPerPage={page_size}&jobOffset={offset}"
        # Tenants that need the browser fallback at all (verified live:
        # careers.ibm.com) render this page's results in via a separate AJAX
        # call after the initial page load, on its own timeline distinct
        # from the WAF-challenge navigation itself - a plain networkidle
        # wait can resolve (or, post yabot.jobs-browser's networkidle-
        # timeout salvage, get used anyway) before those results exist,
        # silently reading as zero jobs rather than "still loading". Give it
        # a real per-page completion signal instead of guessing.
        html = _fetch_page_html(page_url, wait_for_selector='a[href*="/careers/JobDetail"]')
        if html is None:
            break
        links = dict.fromkeys(_JOB_LINK_RE.findall(html))
        if not links:
            break
        urls.extend(links)
        if len(links) < page_size:
            break
        offset += len(links)
    return urls[:_AVATURE_MAX_JOBS]


def extract(html: str) -> ExtractedJobFields:
    """Pull location and the *complete* description straight from the
    rendered page's own template - see the module comment on
    _AVATURE_ARTICLE_RE for why Avature's JSON-LD can't be trusted for
    either field.
    """
    location_match = _AVATURE_LOCATION_RE.search(html)
    location = clean_text(location_match.group(1)) if location_match else _location_from_fields(html)

    sections = []
    for article_html in _AVATURE_ARTICLE_RE.findall(html):
        heading_match = _AVATURE_HEADING_RE.search(article_html)
        if heading_match is None:
            continue  # the location/department/date/ref# header has no heading - not a content section
        heading = clean_text(heading_match.group(1))
        body = html_to_formatted_text(article_html[heading_match.end() :])
        sections.append(f"## {heading}\n\n{body}" if body else f"## {heading}")

    return ExtractedJobFields(location=location, description="\n\n".join(sections) or None)


def scan_job_url(url: str) -> ScanResult | None:
    if not _JOB_DETAIL_URL_RE.search(url):
        return None
    html = _fetch_job_detail_html(url)
    if html is None:
        return ScanResult(success=False, error=f"Failed to fetch {url}: direct fetch and browser render both failed")
    if _AVATURE_META_SIGNATURE not in html:
        return None  # URL path shape was a coincidence; not actually Avature.

    job_postings = base.extract_json_ld_postings(html)
    job_ld = job_postings[0] if job_postings else None
    fields = extract(html)

    title = (job_ld.get("title") if job_ld else None) or base.og_title(html) or base.fallback_title(html)
    hiring_org = job_ld.get("hiringOrganization") if job_ld else None
    company_name = (
        clean_text(hiring_org.get("name"))
        if isinstance(hiring_org, dict)
        else base.og_site_name(html) or _company_name_from_header(html)
    )
    description = fields.description or base.fallback_description(html) or base.og_description(html)

    salary_min, salary_max, salary_currency = base.job_ld_salary(job_ld) if job_ld else (None, None, None)
    if salary_min is None and salary_max is None:
        text_min, text_max, text_currency = base.salary_from_text(description)
        if text_min is not None:
            salary_min, salary_max = text_min, text_max
            salary_currency = text_currency or salary_currency

    return ScanResult(
        success=True,
        title=clean_text(title),
        description=description,
        company_name=company_name,
        location=fields.location,
        workplace_type=WorkplaceType.UNKNOWN,
        employment_type=base.job_ld_employment_type(job_ld) if job_ld else EmploymentType.UNKNOWN,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=base.job_ld_posted_at(job_ld) if job_ld else None,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


# No to_board_url — Avature has no single shared host to canonicalize to
# beyond the tenant subdomain already in the submitted URL; board_url is
# stored verbatim, same reasoning as every other white-label-style adapter
# here.
ADAPTER = AtsAdapter(
    AtsType.AVATURE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
    embedded_match=_detect_embedded,
    scan_job_url=scan_job_url,
)
