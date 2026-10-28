"""Google search results per page and day, from the Search Console API
(spec §31, PR-16), into SearchPerformanceDay. Run nightly by
collection_maintenance.py.

Reads settings.search_console_site with the runtime's own Google
credentials (the jobs' service account, a Restricted user on the property)
over the plain REST API — google-auth for the token, httpx for the call, no
client library. The first run fetches the last BACKFILL_DAYS; after that
the last REFRESH_DAYS, since Google keeps revising its most recent days.
Each fetched day replaces what was stored for it.

Report-only for now: page totals show on the admin page
(collection_lifecycle records them), but the qualification score's demand
part stays unscored until there's enough history to judge it (Phase 3).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Iterable
from urllib.parse import quote, urlsplit

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.search_performance import SearchPerformanceDay
from app.services.static_job_pages import eastern

logger = logging.getLogger("app.search_console")

API = "https://www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
ROW_LIMIT = 25_000  # the API's most per request
BACKFILL_DAYS = 90
REFRESH_DAYS = 5
TOTALS_DAYS = 28


@dataclass(frozen=True)
class PageTotals:
    clicks: int = 0
    impressions: int = 0


def access_token() -> str:
    import google.auth
    import google.auth.transport.requests

    credentials, _ = google.auth.default(scopes=[SCOPE])
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token


def page_path(url: str) -> str | None:
    """The stored form of a result's URL: its path on yabot.jobs without the
    leading slash, or None for another host."""
    parts = urlsplit(url)
    if parts.hostname not in ("yabot.jobs", "www.yabot.jobs"):
        return None
    return parts.path.strip("/")


def fetch(
    start: date, end: date, token: str, client: httpx.Client, dimensions: tuple[str, ...] = ("date", "page")
) -> list[dict]:
    """Every row by `dimensions` from `start` to `end`, inclusive. Searches
    too rare to show are left out by Google, so query rows add up to less
    than page rows."""
    rows: list[dict] = []
    url = API.format(site=quote(settings.search_console_site, safe=""))
    while True:
        body = {
            "startDate": start.isoformat(), "endDate": end.isoformat(), "dimensions": list(dimensions),
            "rowLimit": ROW_LIMIT, "startRow": len(rows), "dataState": "all",
        }
        response = client.post(url, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=60)
        response.raise_for_status()
        batch = response.json().get("rows", [])
        rows += batch
        if len(batch) < ROW_LIMIT:
            return rows


def ingest(
    db: Session, now: datetime, *, token: Callable[[], str] = access_token, client: httpx.Client | None = None
) -> int:
    """Fetch the days due and replace what's stored for them; the rows
    stored. Commits."""
    today = eastern(now).date()
    has_data = db.scalar(select(func.count()).select_from(SearchPerformanceDay)) > 0
    start = today - timedelta(days=REFRESH_DAYS if has_data else BACKFILL_DAYS)
    with client or httpx.Client() as http:
        rows = fetch(start, today, token(), http)

    # Several URLs can map to one path (a trailing slash, www); they add up.
    totals: dict[tuple[date, str], list[float]] = defaultdict(lambda: [0, 0, 0.0])
    for row in rows:
        day, url = row["keys"]
        path = page_path(url)
        if path is None:
            continue
        t = totals[(date.fromisoformat(day), path)]
        t[0] += row.get("clicks", 0)
        t[1] += row.get("impressions", 0)
        t[2] += row.get("position", 0) * row.get("impressions", 0)  # weighted, averaged below

    db.execute(delete(SearchPerformanceDay).where(SearchPerformanceDay.day >= start))
    db.add_all(
        SearchPerformanceDay(
            day=day, page=path, clicks=int(clicks), impressions=int(impressions),
            position=round(weighted / impressions, 2) if impressions else 0.0,
        )
        for (day, path), (clicks, impressions, weighted) in totals.items()
    )
    db.commit()
    logger.info("Search Console: %d page-day row(s) stored for %s to %s.", len(totals), start, today)
    return len(totals)


def fetch_queries(
    now: datetime, *, token: Callable[[], str] = access_token, client: httpx.Client | None = None
) -> list[dict]:
    """Every (search, page) row over the last TOTALS_DAYS Eastern days, for
    app.services.search_opportunities."""
    today = eastern(now).date()
    with client or httpx.Client() as http:
        return fetch(today - timedelta(days=TOTALS_DAYS - 1), today, token(), http, ("query", "page"))


def page_totals(db: Session, now: datetime, days: int = TOTALS_DAYS) -> dict[str, PageTotals]:
    """{path: clicks and impressions} over the last `days` Eastern days."""
    since = eastern(now).date() - timedelta(days=days)
    rows = db.execute(
        select(SearchPerformanceDay.page, func.sum(SearchPerformanceDay.clicks), func.sum(SearchPerformanceDay.impressions))
        .where(SearchPerformanceDay.day > since)
        .group_by(SearchPerformanceDay.page)
    ).all()
    return {page: PageTotals(int(clicks), int(impressions)) for page, clicks, impressions in rows}


def collection_totals(base: str, totals: dict[str, PageTotals]) -> PageTotals:
    """A collection page's totals across its pagination: `base` and
    `base`/page/<n>, not the pages beneath it (its remote page)."""
    prefix = f"{base}/page/"
    pages: Iterable[PageTotals] = (t for path, t in totals.items() if path == base or path.startswith(prefix))
    clicks = impressions = 0
    for t in pages:
        clicks += t.clicks
        impressions += t.impressions
    return PageTotals(clicks, impressions)
