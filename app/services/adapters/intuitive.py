import re
from xml.etree import ElementTree

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, get_with_retry, limit_job_urls

_INTUITIVE_URL_RE = re.compile(r"careers\.intuitive\.com", re.IGNORECASE)
_INTUITIVE_SITEMAP_URL = "https://careers.intuitive.com/sitemap.xml"
# The sitemap mirrors every requisition under five locale prefixes
# (en/es/de/jp/bg, 711 postings each, verified live) — only "en" is crawled,
# or every posting would be scanned five times over as five "different"
# URLs pointing at the same underlying requisition.
_EN_JOB_URL_RE = re.compile(r"^https://careers\.intuitive\.com/en/jobs/\d+/", re.IGNORECASE)


def _match(url: str) -> str | None:
    # Single-company in-house career site (like apple.py/google.py) — the
    # domain alone is the signal, board_key is an unused fixed constant.
    return "intuitive" if _INTUITIVE_URL_RE.search(url) else None


def _fetch_jobs(_board_key: str) -> list[str]:
    # No public jobs API, but the root career-site page (and every job
    # detail page) sits behind a Cloudflare JS challenge a plain httpx
    # request can't pass — sitemap.xml is unprotected (verified live) and
    # lists every current requisition directly, so crawling never needs the
    # browser-render fallback at all, unlike scanning an individual posting.
    response = get_with_retry(_INTUITIVE_SITEMAP_URL, timeout=TIMEOUT)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = (loc.text for loc in root.findall(".//sm:loc", ns) if loc.text and _EN_JOB_URL_RE.match(loc.text))
    return limit_job_urls(urls)[:DEFAULT_MAX_JOBS_PER_CRAWL]


# No scan_job_url: every job page publishes a complete schema.org JobPosting
# JSON-LD block once past Cloudflare's challenge (verified live), so
# job_scanner.py's generic default scanner already extracts everything
# needed — its own base.fetch_html already falls back to the browser-render
# service on the plain-httpx 403 the challenge produces. A platform-specific
# scan_job_url here would just be duplicating that generic path for no gain.
ADAPTER = AtsAdapter(
    AtsType.INTUITIVE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://careers.intuitive.com/en/jobs/",
)
