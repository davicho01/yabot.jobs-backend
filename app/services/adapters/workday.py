import re
from typing import Any

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import (
    DEFAULT_MAX_JOBS_PER_CRAWL,
    RECENT_WINDOW_DAYS,
    TIMEOUT,
    AtsAdapter,
    ExtractedJobFields,
    ScanResult,
    post_with_retry,
)
from app.services.adapters.text import clean_text, html_to_formatted_text

_WORKDAY_JOBS_URL = "https://{company}.{instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs"
_WORKDAY_JOB_BASE_URL = "https://{company}.{instance}.myworkdayjobs.com/{site}"
# Workday paginates 20 jobs per request; a large company can have 1000+ open
# roles. Cap discovery per crawl rather than fully paginating every run —
# already-known URLs are deduped either way, so a cap just bounds how many
# requests one crawl makes to Workday, not what gets found over time.
_WORKDAY_PAGE_SIZE = 20
_WORKDAY_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
# postedOn has no exact date, just a relative label — "Posted Today",
# "Posted Yesterday", "Posted N Days Ago", capping out at "Posted 30+ Days
# Ago" (all four verified live against salesforce.wd12.myworkdayjobs.com).
# The "+" on the capped form is dropped rather than parsed specially: 30 is
# already >= any sane RECENT_WINDOW_DAYS, so treating "30+" as exactly 30
# still excludes it correctly.
_WORKDAY_POSTED_AGE_RE = re.compile(r"^Posted (?:(Today)|(Yesterday)|(\d+)\+? Days? Ago)$", re.IGNORECASE)
# Workday needs three identifiers, not one: the company slug, the Workday
# instance number (e.g. "wd12" — not visible in the careers URL, varies per
# company), and the career site name — joined "/" into one board_key (e.g.
# "salesforce/wd12/External_Career_Site") since board_key is a single
# string, not a structured value.
#
# The site-name group excludes quotes/whitespace/angle brackets, not just
# "/" and "?" — _detect_embedded runs this against a full HTML document
# (careers.toyota.com verified live), where the URL is followed immediately
# by a closing quote and more attributes with no "/" for a long stretch;
# without the tighter class the match ran on past the real site name into
# surrounding markup.
_WORKDAY_URL_RE = re.compile(
    r"([a-zA-Z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?\s\"'<>]+)", re.IGNORECASE
)


def _match(url: str) -> str | None:
    match = _WORKDAY_URL_RE.search(url)
    if not match:
        return None
    company, instance, site = match.groups()
    return f"{company}/{instance}/{site}"


def _board_url(board_key: str) -> str:
    company, instance, site = board_key.split("/")
    return _WORKDAY_JOB_BASE_URL.format(company=company, instance=instance, site=site)


def _posted_age_days(posted_on: str | None) -> int | None:
    if not posted_on:
        return None
    match = _WORKDAY_POSTED_AGE_RE.match(posted_on)
    if not match:
        return None
    today, yesterday, days = match.groups()
    if today:
        return 0
    if yesterday:
        return 1
    return int(days)


def _fetch_jobs(board_key: str) -> list[str]:
    # Free, public, unauthenticated API — no key required.
    company, instance, site = board_key.split("/")
    jobs_url = _WORKDAY_JOBS_URL.format(company=company, instance=instance, site=site)
    job_base_url = _WORKDAY_JOB_BASE_URL.format(company=company, instance=instance, site=site)

    urls: list[str] = []
    offset = 0
    # Some tenants' Workday configuration omits postedOn from the
    # career-site API response entirely (verified live: Memorial
    # Healthcare System, 566 real open roles, every jobPostings entry
    # missing the key) — filtering by recency there wouldn't exclude old
    # postings, it'd silently exclude *every* posting, since
    # _posted_age_days(None) is always None. Detected from the first page;
    # None means "not yet known."
    filter_by_recency: bool | None = None
    while len(urls) < _WORKDAY_MAX_JOBS:
        response = post_with_retry(
            jobs_url,
            json={"appliedFacets": {}, "limit": _WORKDAY_PAGE_SIZE, "offset": offset, "searchText": ""},
            headers={"Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        postings = response.json().get("jobPostings", [])
        if not postings:
            break

        if filter_by_recency is None:
            filter_by_recency = any(p.get("postedOn") for p in postings)

        if filter_by_recency:
            # Workday's default sort is newest-first (verified: offset=0
            # was entirely "Posted Today", offset=300 was entirely "Posted
            # 7 Days Ago" — no interleaving). Stop as soon as a page
            # contains anything outside RECENT_WINDOW_DAYS instead of
            # always paginating to _WORKDAY_MAX_JOBS — far fewer requests
            # per crawl, and correct regardless of how many jobs the
            # company has total.
            recent_postings = [
                p
                for p in postings
                if (age := _posted_age_days(p.get("postedOn"))) is not None and age < RECENT_WINDOW_DAYS
            ]
            urls.extend(
                job_base_url + posting["externalPath"] for posting in recent_postings if posting.get("externalPath")
            )
            if len(recent_postings) < len(postings) or len(postings) < _WORKDAY_PAGE_SIZE:
                break  # hit an older posting, or this was the last page
        else:
            # No recency signal available for this tenant at all — capture
            # every posting up to the shared cap instead, same as any
            # other adapter with no postedOn-equivalent to filter on.
            urls.extend(
                job_base_url + posting["externalPath"] for posting in postings if posting.get("externalPath")
            )
            if len(postings) < _WORKDAY_PAGE_SIZE:
                break
        offset += _WORKDAY_PAGE_SIZE

    # _WORKDAY_MAX_JOBS is a safety net, not the normal stopping point — it
    # only bites if a company posts an unusually large batch within the
    # window.
    return urls[:_WORKDAY_MAX_JOBS]


def _detect_embedded(url: str) -> str | None:
    # Some companies front their real Workday board with their own
    # marketing/career-site domain (e.g. careers.freedommortgage.com, a
    # Phenom People site) and only link out to the myworkdayjobs.com URL
    # from an "apply" link buried in a JSON blob server-rendered into the
    # page — verified live: _WORKDAY_URL_RE matches that embedded URL just
    # as well as a real address-bar one, no extra parsing needed.
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return _match(response.text)


# Workday job pages do embed schema.org JobPosting JSON-LD, so they never
# reach job_scanner.py's no-JSON-LD fallback branch — but Workday generates
# that JSON-LD's `description` field as plain text with every tag
# stripped, not HTML, so it has no paragraph breaks, headings, or bullet
# points left to convert into Markdown (verified against a live posting:
# the JSON-LD description was one unbroken run of sentences). The same
# job's public `wday/cxs` JSON API — the same one Workday's own SPA calls
# client-side, keyed by the visible job path — returns the original
# `jobPostingInfo.jobDescription` HTML with its structure intact.
#
# Also used to sniff for a Workday URL embedded in a branded career site's
# raw HTML (a marketing domain that fronts a real Workday board, linking
# out to it from an "apply"/"login" href — verified live against
# careers.stryker.com, hence extract() trying both the URL and the raw
# html below). The job_path group excludes quotes/whitespace/angle
# brackets, not just "?" and "#", so it stops at the href's closing quote
# instead of running on into the surrounding markup when matched against a
# full HTML document rather than a bare URL.
_JOB_URL_RE = re.compile(
    r"([a-zA-Z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?\s\"'<>]+)(/job/[^?#\s\"'<>]+)",
    re.IGNORECASE,
)
_JOB_DETAIL_URL = "https://{company}.{instance}.myworkdayjobs.com/wday/cxs/{company}/{site}{job_path}"


def _fetch_job_data(url: str) -> dict[str, Any] | None:
    match = _JOB_URL_RE.search(url)
    if match is None:
        return None
    company, instance, site, job_path = match.groups()
    try:
        response = httpx.get(
            _JOB_DETAIL_URL.format(company=company, instance=instance, site=site, job_path=job_path),
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    data = response.json()
    return data if isinstance(data, dict) else None


def _description_of(job_data: dict[str, Any]) -> str | None:
    posting_info = job_data.get("jobPostingInfo")
    description = posting_info.get("jobDescription") if isinstance(posting_info, dict) else None
    return html_to_formatted_text(description) if isinstance(description, str) else None


def extract(url: str, html: str) -> ExtractedJobFields | None:
    job_data = _fetch_job_data(url) or _fetch_job_data(html)
    if job_data is None:
        return None
    return ExtractedJobFields(description=_description_of(job_data))


def scan_job_url(url: str) -> ScanResult | None:
    # Cheap, URL-only gate: a real myworkdayjobs.com link. A branded
    # marketing domain fronting a real Workday board (see extract()'s own
    # docstring, e.g. careers.stryker.com) has no such signal in the URL
    # itself — recovering it needs a speculative fetch job_scanner.py's
    # dispatch loop doesn't pay for every unmatched URL, so that case falls
    # through to the generic default scanner instead, which still reads the
    # branded page's own real (if plainer) JSON-LD correctly; it only misses
    # this adapter's richer wday/cxs description.
    if _match(url) is None:
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    html = page.text
    job_postings = base.extract_json_ld_postings(html)
    job_ld = job_postings[0] if job_postings else None
    fields = extract(url, html)

    hiring_org = job_ld.get("hiringOrganization") if job_ld else None
    company_name = hiring_org.get("name") if isinstance(hiring_org, dict) else None
    description = (
        (fields.description if fields else None)
        or (html_to_formatted_text(job_ld.get("description")) if job_ld else None)
        or base.fallback_description(html)
    )
    salary_min, salary_max, salary_currency = base.job_ld_salary(job_ld) if job_ld else (None, None, None)
    if salary_min is None and salary_max is None:
        text_min, text_max, text_currency = base.salary_from_text(description)
        if text_min is not None:
            salary_min, salary_max = text_min, text_max
            salary_currency = text_currency or salary_currency

    return ScanResult(
        success=True,
        title=(clean_text(job_ld.get("title")) if job_ld else None) or base.fallback_title(html),
        description=description,
        company_name=clean_text(company_name),
        location=base.job_ld_location(job_ld) if job_ld else None,
        workplace_type=base.job_ld_workplace_type(job_ld) if job_ld else WorkplaceType.UNKNOWN,
        employment_type=base.job_ld_employment_type(job_ld) if job_ld else EmploymentType.UNKNOWN,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=base.job_ld_posted_at(job_ld) if job_ld else None,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.WORKDAY,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=_board_url,
    embedded_match=_detect_embedded,
    scan_job_url=scan_job_url,
)
