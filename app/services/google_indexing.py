"""Google Indexing API: after each generate_static_job_pages run, ask Google
to crawl the best of the job pages it just published, instead of waiting for
it to find them in the sitemap. Google supports this API only for pages with
JobPosting markup, which ours have (app.services.static_job_pages).

The quota is small next to what we publish — 200 notifications a day per
GCP project by default against ~15k new pages a weekday — so each run spends
its share of what's left today (spread over the day's scheduled runs, so the
first run doesn't take it all) on the pages that score highest
(static_job_pages.indexing_score: salary listed, known company, remote),
newest first. Removed pages get a URL_DELETED only with budget to spare:
they already serve noindex, so Google drops them on its own next crawl.

How much of today's quota is spent lives in _meta/indexing.json in the page
store, keyed by Google's quota day (it resets at midnight Pacific).

Prod auth is the function's own service account, which must be an Owner of
the site in Search Console. Off unless settings.google_indexing_enabled.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

import httpx
from google.auth import default as google_auth_default
from google.auth.transport.requests import Request as GoogleAuthRequest

from app.core.config import settings
from app.services.static_pages import read_json, write_json

logger = logging.getLogger("app.google_indexing")

SCOPE = "https://www.googleapis.com/auth/indexing"
PUBLISH_URL = "https://indexing.googleapis.com/v3/urlNotifications:publish"
STATE_KEY = "_meta/indexing.json"
# Google's quota day.
QUOTA_TZ = ZoneInfo("America/Los_Angeles")
# The scheduled runs (deploy/gcloud-deploy.sh, "0 9-21/2 * * 1-5", Eastern).
SCHEDULE_TZ = ZoneInfo("America/New_York")
RUN_HOURS = (9, 11, 13, 15, 17, 19, 21)
TIMEOUT = 15.0


@dataclass
class IndexingResult:
    updated: int = 0
    deleted: int = 0
    failed: int = 0
    left_today: int = 0
    stopped: str | None = None  # why it stopped early ("quota", "forbidden")


def _access_token() -> str:
    credentials, _ = google_auth_default(scopes=[SCOPE])
    credentials.refresh(GoogleAuthRequest())
    return credentials.token


def run_budget(remaining: int, now: datetime) -> int:
    """This run's share of what's left today: an even split over the
    scheduled runs still to come, this one included. A run outside the
    schedule (by hand, late) may spend all of it."""
    hour = now.astimezone(SCHEDULE_TZ).hour
    runs_left = sum(1 for h in RUN_HOURS if h >= hour) or 1
    return math.ceil(remaining / runs_left)


def pick(new_pages: Iterable[tuple[int, date, str]], removed_paths: Iterable[str], budget: int) -> list[tuple[str, str]]:
    """[(path, type)] to send: new pages by score, then newest day, then
    path; then removed pages, while the budget lasts."""
    ranked = sorted(new_pages, key=lambda p: (-p[0], -p[1].toordinal(), p[2]))
    picks = [(path, "URL_UPDATED") for _, _, path in ranked]
    picks += [(path, "URL_DELETED") for path in sorted(removed_paths)]
    return picks[: max(budget, 0)]


def notify(
    new_pages: Iterable[tuple[int, date, str]],
    removed_paths: Iterable[str],
    *,
    now: datetime | None = None,
    client: httpx.Client | None = None,
    token: Callable[[], str] = _access_token,
) -> IndexingResult:
    now = now or datetime.now(timezone.utc)
    quota = settings.google_indexing_daily_quota
    today = now.astimezone(QUOTA_TZ).date().isoformat()
    state = read_json(STATE_KEY) or {}
    sent = state.get("sent", 0) if state.get("day") == today else 0
    remaining = max(quota - sent, 0)
    picks = pick(new_pages, removed_paths, run_budget(remaining, now))

    result = IndexingResult()
    if picks:
        headers = {"Authorization": f"Bearer {token()}"}
        owns_client = client is None
        client = client or httpx.Client(timeout=TIMEOUT)
        try:
            for path, kind in picks:
                url = f"{settings.seo_pages_base_url}/{path}"
                try:
                    response = client.post(PUBLISH_URL, json={"url": url, "type": kind}, headers=headers)
                except httpx.HTTPError as exc:
                    logger.warning("Indexing API request for %s failed: %s", url, exc)
                    result.failed += 1
                    continue
                if response.status_code == 429:
                    # Google says today's quota is gone, whatever our count says.
                    sent, result.stopped = quota, "quota"
                    break
                if response.status_code == 403:
                    logger.error(
                        "Indexing API refused (403): is this service account an Owner of %s in Search Console? %s",
                        settings.seo_pages_base_url, response.text[:300],
                    )
                    result.stopped = "forbidden"
                    break
                if response.is_success:
                    sent += 1
                    if kind == "URL_UPDATED":
                        result.updated += 1
                    else:
                        result.deleted += 1
                else:
                    logger.warning("Indexing API %s for %s: %s", response.status_code, url, response.text[:300])
                    result.failed += 1
        finally:
            if owns_client:
                client.close()
        write_json(STATE_KEY, {"day": today, "sent": sent})

    result.left_today = max(quota - sent, 0)
    return result
