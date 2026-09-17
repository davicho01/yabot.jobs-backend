import re

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, RECENT_WINDOW_DAYS, TIMEOUT, AtsAdapter, post_with_retry

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
_WORKDAY_URL_RE = re.compile(
    r"([a-zA-Z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?]+)", re.IGNORECASE
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

        # Workday's default sort is newest-first (verified: offset=0 was
        # entirely "Posted Today", offset=300 was entirely "Posted 7 Days
        # Ago" — no interleaving). Stop as soon as a page contains anything
        # outside RECENT_WINDOW_DAYS instead of always paginating to
        # _WORKDAY_MAX_JOBS — far fewer requests per crawl, and correct
        # regardless of how many jobs the company has total.
        recent_postings = [
            p for p in postings if (age := _posted_age_days(p.get("postedOn"))) is not None and age < RECENT_WINDOW_DAYS
        ]
        urls.extend(
            job_base_url + posting["externalPath"] for posting in recent_postings if posting.get("externalPath")
        )
        if len(recent_postings) < len(postings) or len(postings) < _WORKDAY_PAGE_SIZE:
            break  # hit an older posting, or this was the last page
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


ADAPTER = AtsAdapter(
    AtsType.WORKDAY,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=_board_url,
    embedded_match=_detect_embedded,
)
