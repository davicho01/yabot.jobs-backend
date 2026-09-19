import re
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, get_with_retry
from app.services.browser_fetch import fetch_rendered_page

_AVATURE_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_AVATURE_URL_RE = re.compile(r"([a-zA-Z0-9-]+\.avature\.net)", re.IGNORECASE)
_JOB_LINK_RE = re.compile(r'href="(https?://[^"?]+/careers/JobDetail/[^"?]+)', re.IGNORECASE)
_DEFAULT_PAGE_SIZE = 6  # observed tenant-configured default (verified: Synopsys)
# Some tenants white-label Avature entirely onto their own domain with no
# *.avature.net hop anywhere in the page (verified live: careers.lululemon.com)
# — _AVATURE_URL_RE never matches, static or embedded. The page still carries
# a set of `<meta name="avature.portal...">` tags Avature itself injects
# (portal.id/name/urlPath), which is otherwise-unused text unlikely to
# collide with another platform, and the existing /careers/JobDetail/
# path + SearchJobs pagination works unmodified on the company's own host.
_AVATURE_META_SIGNATURE = 'name="avature.portal'


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


def _fetch_page_html(url: str) -> str | None:
    try:
        response = get_with_retry(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        if response.text.strip():
            return response.text
    except httpx.HTTPError:
        pass
    # Some tenants (verified live: delta.avature.net) front every path —
    # including the plain /careers listing and even the RSS feed endpoint,
    # not just some deeper page — with an AWS WAF JS challenge that returns
    # an empty 202 to any plain httpx request, regardless of User-Agent or
    # cookies. Unlike every other adapter in this codebase, a real browser
    # render is the only way to get past it at all here, not just a
    # nicer-to-have fallback for an occasional blocked page (verified: other
    # tenants like synopsys.avature.net need no browser at all).
    rendered = fetch_rendered_page(url)
    return rendered.html if rendered else None


def _resolve_careers_url(host: str) -> str | None:
    base_url = f"https://{host}/careers"
    try:
        response = get_with_retry(base_url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        if response.text.strip():
            return str(response.url).split("?")[0].rstrip("/")
    except httpx.HTTPError:
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
        html = _fetch_page_html(page_url)
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
)
