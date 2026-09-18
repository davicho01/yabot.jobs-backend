import re
from datetime import date, datetime, timezone
from typing import Any

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import (
    DEFAULT_MAX_JOBS_PER_CRAWL,
    EMPLOYMENT_TYPE_MAP,
    TIMEOUT,
    AtsAdapter,
    ExtractedJobFields,
    ScanResult,
    get_with_retry,
    limit_job_urls,
)
from app.services.adapters.text import MAX_LOCATION_LENGTH, html_to_formatted_text

# "FullStack Connect" (per its own <title>) is FullStack's own in-house
# recruiting platform, not a multi-tenant ATS other companies use — same
# single-company shape as amazon.py/google.py/apple.py. Real company name
# verified against fullstacklabs.co's own <title> ("... | FullStack") and
# the posting body copy itself ("FullStack is your AI-native engineering
# partner...").
_COMPANY_NAME = "FullStack"
_FULLSTACK_URL_RE = re.compile(r"talent\.fullstack\.com", re.IGNORECASE)
_JOB_ID_RE = re.compile(r"talent\.fullstack\.com/jobs/([0-9a-fA-F-]{36})", re.IGNORECASE)

# talent.fullstack.com is an empty SPA shell (no JSON-LD, no og: tags at
# all) backed by a separate API host pulled from its JS bundle
# (api-v1.fullstack.com) — verified live. Both the listing and single-job
# endpoints need the undocumented "/api" prefix (bare "/job-postings/..."
# 404s) and no auth/cookie/Origin header at all, despite the frontend
# always sending one.
_LIST_URL = "https://api-v1.fullstack.com/api/job-postings/public"
_DETAIL_URL = "https://api-v1.fullstack.com/api/job-postings/{id}"
_JOB_URL = "https://talent.fullstack.com/jobs/{id}"
# The listing endpoint 400s ("Invalid take value.") on every page size
# tried except 30 — verified live — so this isn't a tunable request
# parameter, just the server's fixed page size.
_PAGE_SIZE = 30

_WORKPLACE_TYPE_MAP = {
    "remote": WorkplaceType.REMOTE,
    "hybrid": WorkplaceType.HYBRID,
    "on-site": WorkplaceType.ONSITE,
    "onsite": WorkplaceType.ONSITE,
}


def _match(url: str) -> str | None:
    return "fullstack" if _FULLSTACK_URL_RE.search(url) else None


def _fetch_jobs(_board_key: str) -> list[str]:  # noqa: ARG001 - single-company board, fixed key, no key needed
    # order=DESC&orderBy=createdAt is silently accepted but doesn't actually
    # change result order (verified live: identical, non-chronological
    # ordering with or without it), and no per-job date is exposed on the
    # listing endpoint anyway — so unlike Apple/Amazon there's no reliable
    # recency cutoff to paginate against. Pagination is bounded by the
    # shared job cap instead, same reasoning as google.py.
    urls: list[str] = []
    page = 1
    while len(urls) < DEFAULT_MAX_JOBS_PER_CRAWL:
        response = get_with_retry(_LIST_URL, params={"page": page, "take": _PAGE_SIZE}, timeout=TIMEOUT)
        response.raise_for_status()
        body = response.json()
        postings = body.get("data")
        if not isinstance(postings, list) or not postings:
            break
        urls.extend(_JOB_URL.format(id=posting["id"]) for posting in postings if isinstance(posting.get("id"), str))
        if not body.get("meta", {}).get("hasNextPage"):
            break
        page += 1
    return limit_job_urls(urls)


def _extract_job_id(url: str) -> str | None:
    match = _JOB_ID_RE.search(url)
    return match.group(1) if match else None


def _fetch_job_data(job_id: str) -> dict[str, Any] | None:
    try:
        response = httpx.get(_DETAIL_URL.format(id=job_id), timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _location_of(job: dict[str, Any]) -> str | None:
    parts = [p for p in (job.get("city"), job.get("state")) if isinstance(p, str) and p.strip()]
    location = ", ".join(dict.fromkeys(parts)) if parts else None
    if location is None:
        region = job.get("region") or job.get("regionGroup")
        location = region if isinstance(region, str) and region.strip() else None
    if location and len(location) > MAX_LOCATION_LENGTH:
        location = location[: MAX_LOCATION_LENGTH - 3] + "..."
    return location


def _workplace_type_of(job: dict[str, Any]) -> str:
    value = job.get("linkedInWorkplaceType")
    if isinstance(value, str):
        return _WORKPLACE_TYPE_MAP.get(value.strip().lower(), WorkplaceType.UNKNOWN)
    return WorkplaceType.UNKNOWN


def _employment_type_of(job: dict[str, Any]) -> str:
    value = job.get("linkedInJobType")
    if isinstance(value, str):
        return EMPLOYMENT_TYPE_MAP.get(value.upper(), EmploymentType.UNKNOWN)
    return EmploymentType.UNKNOWN


def _posted_at_of(job: dict[str, Any]) -> date | None:
    # createdAt on the single-job endpoint is epoch milliseconds (unlike the
    # listing endpoint's ISO-string createdAt, verified live) — a genuine
    # format mismatch between the two endpoints, not a bug on our side.
    created = job.get("createdAt")
    if not isinstance(created, (int, float)) or isinstance(created, bool):
        return None
    try:
        return datetime.fromtimestamp(created / 1000, tz=timezone.utc).date()
    except (ValueError, OverflowError, OSError):
        return None


def _description_of(job: dict[str, Any]) -> str | None:
    sections = job.get("sections")
    if not isinstance(sections, list):
        return None
    # isDetached marks a section overridden/removed for this specific
    # posting (verified against the shape of a live posting with
    # hasOverrides=true) — excluded the same way a detached override would
    # be on the live page.
    ordered = sorted(
        (
            s
            for s in sections
            if isinstance(s, dict) and not s.get("isDetached") and isinstance(s.get("body"), str) and s["body"].strip()
        ),
        key=lambda s: s.get("order", 0),
    )
    html = "\n\n".join(f"<h3>{s['title']}</h3>{s['body']}" if s.get("title") else s["body"] for s in ordered)
    return html_to_formatted_text(html) if html else None


def extract(url: str, _html: str) -> ExtractedJobFields | None:
    # The job page itself is an empty SPA shell with nothing to scrape —
    # every field comes from the detail API keyed off the job id in the
    # URL, same reasoning as gem.py/nlx.py.
    job_id = _extract_job_id(url)
    if job_id is None:
        return None
    job = _fetch_job_data(job_id)
    if job is None:
        return None
    title = job.get("jobTitle")
    return ExtractedJobFields(
        title=title if isinstance(title, str) else None,
        description=_description_of(job),
        company_name=_COMPANY_NAME,
        location=_location_of(job),
        workplace_type=_workplace_type_of(job),
        employment_type=_employment_type_of(job),
        posted_at=_posted_at_of(job),
    )


def scan_job_url(url: str) -> ScanResult | None:
    if _extract_job_id(url) is None:
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    html = page.text
    fields = extract(url, html)
    if fields is None:
        # No useful generic fallback — the page itself carries no JSON-LD,
        # og: tags, or even a job-specific <title> (every route shares the
        # same static "FullStack Connect" shell), so falling back to it
        # would be actively wrong rather than merely incomplete.
        return ScanResult(success=False, error="Couldn't fetch this FullStack job posting's data.")

    salary_min, salary_max, salary_currency = base.salary_from_text(fields.description)
    return ScanResult(
        success=True,
        title=fields.title,
        description=fields.description,
        company_name=fields.company_name,
        location=fields.location,
        workplace_type=fields.workplace_type,
        employment_type=fields.employment_type,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=fields.posted_at,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.FULLSTACK,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://talent.fullstack.com",
    scan_job_url=scan_job_url,
)
