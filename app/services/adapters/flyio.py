import re
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from app.models.enums import AtsType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry
from app.services.adapters.text import clean_text, extract_balanced_tag, html_to_formatted_text

_FLYIO_FEED_URL = "https://fly.io/jobs/feed.xml"
# The dt's own text ("Work From") is the stable marker here, not any of the
# surrounding Tailwind utility classes, which look auto-generated/likely to
# shift — verified live: this exact dt/dd pair appears twice per page (a
# mobile-header copy and a desktop-sidebar copy of the same value), so any
# one match is enough.
_LOCATION_RE = re.compile(r"Work From\s*</dt>.*?<div>\s*<div>\s*([^<]*?)\s*</div>", re.IGNORECASE | re.DOTALL)
_TITLE_RE = re.compile(r"<h1[^>]*>\s*(.*?)\s*</h1>", re.IGNORECASE | re.DOTALL)


def _match(url: str) -> str | None:
    parts = urlsplit(url)
    return "flyio" if parts.netloc.lower() == "fly.io" and parts.path.startswith("/jobs/") else None


def _fetch_jobs(_board_key: str) -> list[str]:
    # A small, static-site-generator-built board (in-house, not any ATS) —
    # its own Atom feed lists every current opening directly, no pagination
    # needed (verified live: 4 total postings).
    response = get_with_retry(_FLYIO_FEED_URL, timeout=TIMEOUT)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    return [
        link.get("href")
        for entry in root.findall("a:entry", ns)
        for link in entry.findall("a:link[@rel='alternate']", ns)
        if link.get("href")
    ]


def _description_of(html: str) -> str | None:
    article_match = re.search(r"<article\b", html)
    if article_match is None:
        return None
    article = extract_balanced_tag(html, article_match.start(), "article")
    if article is None:
        return None
    section_match = re.search(r'<section\b[^>]*class="relative[^"]*"', article)
    if section_match is None:
        return None
    inner = extract_balanced_tag(article, section_match.start(), "section")
    return html_to_formatted_text(inner) if inner else None


def scan_job_url(url: str) -> ScanResult | None:
    if _match(url) is None or url.rstrip("/").endswith("/jobs"):
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    html = page.text

    title_match = _TITLE_RE.search(html)
    location_match = _LOCATION_RE.search(html)
    location = clean_text(location_match.group(1)) if location_match else None
    workplace_type = WorkplaceType.REMOTE if location and location.strip().lower() == "remote" else WorkplaceType.UNKNOWN

    return ScanResult(
        success=True,
        title=clean_text(title_match.group(1)) if title_match else base.fallback_title(html),
        description=_description_of(html),
        company_name="Fly.io",
        location=location,
        workplace_type=workplace_type,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.FLYIO,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://fly.io/jobs/",
    scan_job_url=scan_job_url,
)
