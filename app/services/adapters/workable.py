import re

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, limit_job_urls, AtsAdapter, get_with_retry

_WORKABLE_JOBS_URL = "https://apply.workable.com/api/v1/widget/accounts/{board_key}"
# Requires the account-prefixed URL shape (apply.workable.com/{account}/j/...,
# e.g. as linked from a LinkedIn "Apply" redirect) — the bare shortlink
# form (apply.workable.com/j/{code}) resolves to the same job but doesn't
# carry the account slug this needs, so it just won't match.
_WORKABLE_ACCOUNT_JOB_RE = re.compile(r"apply\.workable\.com/([^/?]+)/j/", re.IGNORECASE)
# The bare board page (no specific job), e.g. as stored in
# CrawlSource.board_url — (?!j/) excludes the ambiguous shortlink shape
# above, which the pattern above already claims.
_WORKABLE_BOARD_RE = re.compile(r"apply\.workable\.com/(?!j/)([^/?]+)/?(?:\?|$)", re.IGNORECASE)
# Workable's short link form (apply.workable.com/j/{code}) carries no
# account slug, unlike the account-prefixed form above — but it
# 301-redirects to that same account-prefixed URL (verified live), so
# following the redirect and re-running the normal match against the
# resolved URL recovers the account slug.
_WORKABLE_SHORTLINK_RE = re.compile(r"apply\.workable\.com/j/[a-zA-Z0-9]+", re.IGNORECASE)


def _match(url: str) -> str | None:
    match = _WORKABLE_ACCOUNT_JOB_RE.search(url) or _WORKABLE_BOARD_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    # Free, public, unauthenticated API — no key required (the same feed
    # that powers Workable's embeddable "jobs widget").
    response = get_with_retry(_WORKABLE_JOBS_URL.format(board_key=board_key), timeout=TIMEOUT)
    response.raise_for_status()
    jobs = response.json().get("jobs", [])
    return limit_job_urls(job["url"] for job in jobs if job.get("url"))


def _detect_embedded(url: str) -> str | None:
    if not _WORKABLE_SHORTLINK_RE.search(url):
        return None
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return _match(str(response.url))


ADAPTER = AtsAdapter(
    AtsType.WORKABLE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://apply.workable.com/{key}/",
    embedded_match=_detect_embedded,
)
