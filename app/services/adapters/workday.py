import re

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter

_WORKDAY_JOBS_URL = "https://{company}.{instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs"
_WORKDAY_JOB_BASE_URL = "https://{company}.{instance}.myworkdayjobs.com/{site}"
# Workday paginates 20 jobs per request; a large company can have 1000+ open
# roles. Cap discovery per crawl rather than fully paginating every run —
# already-known URLs are deduped either way, so a cap just bounds how many
# requests one crawl makes to Workday, not what gets found over time.
_WORKDAY_PAGE_SIZE = 20
_WORKDAY_MAX_JOBS = 200
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


def _fetch_jobs(board_key: str) -> list[str]:
    # Free, public, unauthenticated API — no key required.
    company, instance, site = board_key.split("/")
    jobs_url = _WORKDAY_JOBS_URL.format(company=company, instance=instance, site=site)
    job_base_url = _WORKDAY_JOB_BASE_URL.format(company=company, instance=instance, site=site)

    urls: list[str] = []
    offset = 0
    while len(urls) < _WORKDAY_MAX_JOBS:
        response = httpx.post(
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
        # entirely "Posted Today", offset=400 was entirely "Posted 10 Days
        # Ago" — no interleaving). Since we only want today's new postings,
        # stop as soon as a page contains anything older instead of always
        # paginating to _WORKDAY_MAX_JOBS — far fewer requests per crawl,
        # and correct regardless of how many jobs the company has total.
        todays_postings = [p for p in postings if p.get("postedOn") == "Posted Today"]
        urls.extend(
            job_base_url + posting["externalPath"] for posting in todays_postings if posting.get("externalPath")
        )
        if len(todays_postings) < len(postings) or len(postings) < _WORKDAY_PAGE_SIZE:
            break  # hit an older posting, or this was the last page
        offset += _WORKDAY_PAGE_SIZE

    # _WORKDAY_MAX_JOBS is a safety net, not the normal stopping point — it
    # only bites if a company posts an unusually large batch in a single day.
    return urls[:_WORKDAY_MAX_JOBS]


ADAPTER = AtsAdapter(
    AtsType.WORKDAY,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=_board_url,
)
