import re

import httpx

from app.models.enums import AtsType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, ScanResult, get_with_retry
from app.services.adapters.text import html_to_formatted_text

_GLIDEFAST_CAREERS_URL = "https://glidefast.com/careers"
_GLIDEFAST_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_URL_RE = re.compile(r"glidefast\.com/careers", re.IGNORECASE)
_JOB_LINK_RE = re.compile(r'href="(/careers/jobs/[A-Za-z0-9]+)')
# GlideFast's board is a bespoke HubSpot CMS page (single company, not a
# shared ATS) — job listing links and the job pages themselves are fully
# server-rendered static HTML, verified live, so no browser render or
# external API is needed for either discovery or scanning.
_TITLE_RE = re.compile(r'<h2[^>]*class="mar-0 font-xs-25 top-overlay"[^>]*>\s*<div>\s*(.*?)\s*</div>', re.DOTALL)
_DETAILS_RE = re.compile(
    r'<h2>Job Details</h2>(.*?)<h2 class="mar-0 font-xs-25 top-overlay"', re.IGNORECASE | re.DOTALL
)
_WORKPLACE_TYPE_WORDS = {
    "remote": WorkplaceType.REMOTE,
    "hybrid": WorkplaceType.HYBRID,
    "onsite": WorkplaceType.ONSITE,
    "on-site": WorkplaceType.ONSITE,
}


def _match(url: str) -> str | None:
    return "glidefast" if _URL_RE.search(url) else None


def _fetch_jobs(_board_key: str) -> list[str]:  # noqa: ARG001 - single-company board, fixed key, no key needed
    response = get_with_retry(_GLIDEFAST_CAREERS_URL, timeout=TIMEOUT)
    response.raise_for_status()
    paths = dict.fromkeys(_JOB_LINK_RE.findall(response.text))
    return [f"https://glidefast.com{path}" for path in paths][:_GLIDEFAST_MAX_JOBS]


def _location_and_workplace_type(formatted_description: str) -> tuple[str | None, str]:
    # Operates on the already-Markdown-formatted description (not the raw
    # HTML details block) since GlideFast's "Remote"/city line is separated
    # from the rest by a <br>, not a real newline — splitting the raw HTML
    # on "\n" would just grab the whole first blob of tag soup as one line.
    lines = formatted_description.splitlines()
    first_line = lines[0].strip() if lines else ""
    workplace_type = _WORKPLACE_TYPE_WORDS.get(first_line.lower(), WorkplaceType.UNKNOWN)
    location = None if workplace_type != WorkplaceType.UNKNOWN else (first_line or None)
    return location, workplace_type


def scan_job_url(url: str) -> ScanResult | None:
    if not _URL_RE.search(url) or "/careers/jobs/" not in url:
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    html = page.text
    title_match = _TITLE_RE.search(html)
    title = base.fallback_title(html)
    if title_match:
        title = html_to_formatted_text(title_match.group(1)) or title

    details_match = _DETAILS_RE.search(html)
    description = html_to_formatted_text(details_match.group(1)) if details_match else None
    location, workplace_type = (
        _location_and_workplace_type(description) if description else (None, WorkplaceType.UNKNOWN)
    )
    # The details block's own first line becomes the description's first
    # line too (see html_to_formatted_text above) — drop that duplicate
    # once it's been pulled out into location/workplace_type separately.
    if description and workplace_type != WorkplaceType.UNKNOWN:
        description = "\n".join(description.splitlines()[1:]).lstrip("\n") or description

    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=title,
        description=description or base.fallback_description(html),
        company_name="Everforth GlideFast",
        location=location,
        workplace_type=workplace_type,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.GLIDEFAST,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: _GLIDEFAST_CAREERS_URL,
    scan_job_url=scan_job_url,
)
