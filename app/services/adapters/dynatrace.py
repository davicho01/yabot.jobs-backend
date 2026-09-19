import re
from xml.etree import ElementTree

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, get_with_retry

_DYNATRACE_SITEMAP_INDEX_URL = "https://www.dynatrace.com/careers/sitemap-index.xml"
_DYNATRACE_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_URL_RE = re.compile(r"dynatrace\.com/careers", re.IGNORECASE)
_JOB_URL_RE = re.compile(r"dynatrace\.com/careers/jobs/\d+/?$", re.IGNORECASE)


def _match(url: str) -> str | None:
    return "dynatrace" if _URL_RE.search(url) else None


def _fetch_jobs(_board_key: str) -> list[str]:  # noqa: ARG001 - single-company board, fixed key, no key needed
    # No public API, but Dynatrace's own careers/sitemap-index.xml (verified
    # live) lists every job posting cleanly alongside the site's other
    # careers/marketing pages (locations, "how we hire", ...) — the job
    # entries are the only ones shaped .../careers/jobs/{numeric_id}/, so a
    # path regex separates them out. Individual job pages carry full
    # schema.org JobPosting JSON-LD (title, location, description,
    # datePosted, employmentType all present, verified live), so no
    # scan_job_url is needed here — job_scanner.py's generic default
    # scanner already reads that.
    index = get_with_retry(_DYNATRACE_SITEMAP_INDEX_URL, timeout=TIMEOUT)
    index.raise_for_status()
    index_root = ElementTree.fromstring(index.content)

    urls: list[str] = []
    for loc in index_root.findall(".//sm:sitemap/sm:loc", _SITEMAP_NS):
        if not loc.text:
            continue
        sitemap = get_with_retry(loc.text, timeout=TIMEOUT)
        sitemap.raise_for_status()
        sitemap_root = ElementTree.fromstring(sitemap.content)
        urls.extend(
            u.text
            for u in sitemap_root.findall(".//sm:url/sm:loc", _SITEMAP_NS)
            if u.text and _JOB_URL_RE.search(u.text)
        )
        if len(urls) >= _DYNATRACE_MAX_JOBS:
            break
    return urls[:_DYNATRACE_MAX_JOBS]


ADAPTER = AtsAdapter(
    AtsType.DYNATRACE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://www.dynatrace.com/careers/jobs/",
)
