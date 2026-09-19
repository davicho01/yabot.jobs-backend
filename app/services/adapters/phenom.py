import re
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, get_with_retry

_PHENOM_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
# Phenom People (cdn.phenompeople.com/assets.phenompeople.com) is white-label
# CMS, like Clinch/Oracle Fusion/SuccessFactors — no shared host, each tenant
# on its own domain (careers.gene.com, careers.snowflake.com, verified live).
_PHENOM_SIGNATURE = "phenompeople"
_SITEMAP_RE = re.compile(r"^Sitemap:\s*(\S+)", re.IGNORECASE | re.MULTILINE)


def _sitemap_url(host: str) -> str:
    # The job sitemap sits under a locale-prefixed path that varies per
    # tenant (verified: /us/en/sitemap.xml on both Genentech and Snowflake,
    # but robots.txt is what actually declares it — no hardcoded locale
    # guess needed).
    try:
        response = httpx.get(f"https://{host}/robots.txt", timeout=TIMEOUT)
        response.raise_for_status()
        match = _SITEMAP_RE.search(response.text)
        if match:
            return match.group(1)
    except httpx.HTTPError:
        pass
    return f"https://{host}/sitemap.xml"


def _fetch_jobs(host: str) -> list[str]:
    # No public jobs API discoverable (the client-rendered search page's own
    # /api/apply/v2/jobs endpoint rejects every {domain} value tried,
    # verified live against Genentech — "Tenant not identified"), but the
    # SEO sitemap lists every live posting directly under /job/{id}/{slug},
    # each backed by a full schema.org JobPosting JSON-LD on the detail page
    # (verified live: location, description, employmentType all present) —
    # same shape as clinch.py's sitemap approach.
    response = get_with_retry(_sitemap_url(host), timeout=TIMEOUT)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [
        loc.text
        for loc in root.findall(".//sm:loc", ns)
        if loc.text and "/job/" in urlsplit(loc.text).path
    ]
    return urls[:_PHENOM_MAX_JOBS]


def _detect_embedded(url: str) -> str | None:
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    host = urlsplit(str(response.url)).netloc
    if _PHENOM_SIGNATURE in response.text.lower():
        return host
    try:
        return host if _fetch_jobs(host) else None
    except (httpx.HTTPError, ElementTree.ParseError):
        return None


def _board_key(url: str) -> str | None:
    return urlsplit(url).netloc or None


# No to_board_url — white-labeled with no shared host to canonicalize to,
# same reasoning as Clinch/Oracle Fusion/SuccessFactors; board_url is stored
# verbatim, as submitted.
ADAPTER = AtsAdapter(
    AtsType.PHENOM,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
    embedded_match=_detect_embedded,
)
