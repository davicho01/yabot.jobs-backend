import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import (
    EMPLOYMENT_TYPE_MAP,
    TIMEOUT,
    AtsAdapter,
    ExtractedJobFields,
    ScanResult,
    is_recent_posting,
    limit_job_urls,
    post_with_retry,
)
from app.services.adapters.text import MAX_LOCATION_LENGTH, OG_TITLE_RE, clean_text, html_to_formatted_text

_JOBS_URL = "https://jobs.gem.com/api/public/graphql"
_QUERY = """query JobBoardList($boardId: String!) {
  oatsExternalJobPostings(boardId: $boardId) {
    jobPostings { extId firstPublishedTsSec }
  }
}"""


def _match(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.hostname != "jobs.gem.com":
        return None
    slug = parts.path.strip("/").split("/")[0]
    return slug if re.fullmatch(r"[a-zA-Z0-9_-]+", slug) and slug != "api" else None


def _published_at(job: dict) -> datetime | None:
    timestamp = job.get("firstPublishedTsSec")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _fetch_jobs(board_key: str) -> list[str]:
    # The public board's own GraphQL query returns the complete listing;
    # firstPublishedTsSec is available on the same records without fetching
    # each detail page. HTTP 200 can still contain GraphQL errors.
    response = post_with_retry(
        _JOBS_URL, json={"query": _QUERY, "variables": {"boardId": board_key}}, timeout=TIMEOUT
    )
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        raise ValueError(f"Gem job listing failed: {body['errors']}")
    data = body.get("data") or {}
    listing = data.get("oatsExternalJobPostings")
    if not isinstance(listing, dict) or not isinstance(listing.get("jobPostings"), list):
        raise ValueError("Gem response is missing jobPostings")
    jobs = listing["jobPostings"]
    return limit_job_urls(
        f"https://jobs.gem.com/{board_key}/{job['extId']}"
        for job in jobs
        if isinstance(job.get("extId"), str)
        and re.fullmatch(r"[a-zA-Z0-9_-]+", job["extId"])
        and is_recent_posting(_published_at(job))
    )


# jobs.gem.com's own client only ever calls this GraphQL API for a job's
# structured fields (extracted from its bundled query string, verified
# live) — the static HTML has no JSON-LD, no og:site_name, and no other
# on-page signal for company/location/remote/employment-type at all.
# `locations` (top-level, on the external posting itself) is the location
# this specific extId is posted under; `job.locations` is every location
# the underlying requisition posts to across all of a company's boards, so
# it's not used here. Gem's own employmentType strings (FULL_TIME, ...)
# happen to already match schema.org's, so EMPLOYMENT_TYPE_MAP is reused
# as-is rather than duplicating it under a new name.
def _fetch_job_data(url: str) -> dict[str, Any] | None:
    parts = urlsplit(url)
    path = parts.path.strip("/").split("/")
    if parts.hostname != "jobs.gem.com" or len(path) < 2:
        return None
    query = """query($boardId: String!, $extId: String!) {
      oatsExternalJobPosting(boardId: $boardId, extId: $extId) {
        descriptionHtml compensationHtml jobPostSectionHtml { introHtml outroHtml }
        locations { name isRemote }
        job { employmentType }
      }
    }"""
    try:
        response = httpx.post(
            "https://jobs.gem.com/api/public/graphql",
            json={"query": query, "variables": {"boardId": path[0], "extId": path[1]}},
            timeout=30.0,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("errors"):
            return None
        job = (body.get("data") or {}).get("oatsExternalJobPosting")
        return job if isinstance(job, dict) and job.get("descriptionHtml") else None
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return None


def _description_of(job: dict[str, Any]) -> str | None:
    sections = job.get("jobPostSectionHtml") or {}
    html = "\n".join(
        section for section in (
            sections.get("introHtml"), job.get("descriptionHtml"),
            job.get("compensationHtml"), sections.get("outroHtml"),
        ) if isinstance(section, str)
    )
    # Gem's editor uses empty headings for spacing; avoid rendering
    # these as literal Markdown heading markers in the description.
    html = re.sub(r"<h[1-6]\b[^>]*>\s*(?:<br\s*/?>\s*)*</h[1-6]>", "", html, flags=re.IGNORECASE)
    return html_to_formatted_text(html)


def _location_of(job: dict[str, Any]) -> str | None:
    locations = job.get("locations")
    if not isinstance(locations, list):
        return None
    names = [loc.get("name") for loc in locations if isinstance(loc, dict)]
    location = "; ".join(dict.fromkeys(n for n in names if isinstance(n, str) and n.strip())) or None
    if location and len(location) > MAX_LOCATION_LENGTH:
        location = location[: MAX_LOCATION_LENGTH - 3] + "..."
    return location


def _workplace_type_of(job: dict[str, Any]) -> str:
    locations = job.get("locations")
    if not isinstance(locations, list) or not locations:
        return WorkplaceType.UNKNOWN
    remote_flags = [loc.get("isRemote") for loc in locations if isinstance(loc, dict) and "isRemote" in loc]
    if not remote_flags:
        return WorkplaceType.UNKNOWN
    if all(remote_flags):
        return WorkplaceType.REMOTE
    if not any(remote_flags):
        return WorkplaceType.ONSITE
    return WorkplaceType.HYBRID


def _employment_type_of(job: dict[str, Any]) -> str:
    inner = job.get("job")
    employment_type = inner.get("employmentType") if isinstance(inner, dict) else None
    if isinstance(employment_type, str):
        return EMPLOYMENT_TYPE_MAP.get(employment_type.upper(), EmploymentType.UNKNOWN)
    return EmploymentType.UNKNOWN


# The job page itself never names the company, but every Gem board's own
# page title/og:title reliably follows the "{Company} Careers" shape
# (verified live against several boards, e.g. "Bilt Rewards Careers",
# "Modular Careers") — the only place Gem exposes it at all.
_BOARD_TITLE_TRAILER_RE = re.compile(r"\s+Careers$", re.IGNORECASE)


def _fetch_company_name(url: str) -> str | None:
    parts = urlsplit(url)
    path = parts.path.strip("/").split("/")
    if parts.hostname != "jobs.gem.com" or not path or not path[0]:
        return None
    try:
        response = httpx.get(f"https://jobs.gem.com/{path[0]}", timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    match = OG_TITLE_RE.search(response.text)
    title = clean_text(match.group(1)) if match else None
    if title is None:
        return None
    return _BOARD_TITLE_TRAILER_RE.sub("", title).strip() or None


def extract(url: str, _html: str) -> ExtractedJobFields | None:
    # Gem's job page HTML is a client-rendered SPA shell with no structured
    # data at all — everything comes from the GraphQL API instead, keyed
    # off the URL alone, so the fetched html isn't used here.
    job = _fetch_job_data(url)
    if job is None:
        return None
    return ExtractedJobFields(
        description=_description_of(job),
        company_name=_fetch_company_name(url),
        location=_location_of(job),
        workplace_type=_workplace_type_of(job),
        employment_type=_employment_type_of(job),
    )


def scan_job_url(url: str) -> ScanResult | None:
    parts = urlsplit(url)
    path = parts.path.strip("/").split("/")
    if parts.hostname != "jobs.gem.com" or len(path) < 2 or not path[0] or not path[1]:
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    html = page.text
    # Gem's own GraphQL data (via extract()) always wins when present; the
    # og:description-derived location/workplace-type below is only a
    # fallback for when the API call itself failed, same as the raw
    # <title>/meta-description fallback for title/description.
    fields = extract(url, html)
    og_description_raw = base.og_description_raw(html)
    og_location, og_workplace_type = (
        base.parse_og_description(og_description_raw) if og_description_raw else (None, WorkplaceType.UNKNOWN)
    )
    description = (
        (fields.description if fields else None) or base.fallback_description(html) or base.og_description(html)
    )
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=base.og_title(html) or base.fallback_title(html),
        description=description,
        company_name=(fields.company_name if fields else None) or base.og_site_name(html),
        location=(fields.location if fields else None) or og_location,
        workplace_type=(
            fields.workplace_type if fields and fields.workplace_type != WorkplaceType.UNKNOWN else og_workplace_type
        ),
        employment_type=fields.employment_type if fields else EmploymentType.UNKNOWN,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.GEM,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://jobs.gem.com/{key}",
    scan_job_url=scan_job_url,
)
