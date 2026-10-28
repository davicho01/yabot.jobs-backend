"""Tests for app.services.search_console: fetching from the Search Console
API (faked with httpx.MockTransport), storing page-days, and the totals the
admin page shows (via collection_lifecycle)."""

import json
from datetime import date, timedelta

import httpx
from sqlalchemy import select

import app.models as m
from app.models.enums import CollectionStatus as S
from app.services import collection_lifecycle as cl
from app.services import search_console as sc
from tests.services.test_static_collection_pages import NOW, collection, db, job  # noqa: F401  (fixtures)

TODAY = date(2026, 10, 26)  # NOW, Eastern


def _row(day, url, clicks, impressions, position):
    return {"keys": [day, url], "clicks": clicks, "impressions": impressions, "position": position}


def _client(pages, seen=None):
    """A fake API serving `pages` (lists of rows) one request at a time."""
    replies = iter(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append((request.url.raw_path.decode(), json.loads(request.content), request.headers["Authorization"]))
        return httpx.Response(200, json={"rows": next(replies, [])})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_page_path():
    assert sc.page_path("https://yabot.jobs/jobs/us/software-engineer") == "jobs/us/software-engineer"
    assert sc.page_path("https://www.yabot.jobs/jobs/us/") == "jobs/us"
    assert sc.page_path("https://yabot.jobs/") == ""
    assert sc.page_path("https://example.com/jobs/us") is None


def test_first_run_backfills_and_stores_page_days(db):
    seen = []
    rows = [
        _row("2026-10-24", "https://yabot.jobs/jobs/us/software-engineer", 3, 100, 8.0),
        _row("2026-10-24", "https://yabot.jobs/jobs/us/software-engineer/", 1, 100, 12.0),  # same path
        _row("2026-10-25", "https://yabot.jobs/jobs/us/data-engineer", 0, 40, 20.5),
        _row("2026-10-25", "https://other.example/x", 9, 9, 1.0),
    ]
    assert sc.ingest(db, NOW, token=lambda: "t0k", client=_client([rows], seen)) == 2
    path, body, auth = seen[0]
    assert path == "/webmasters/v3/sites/sc-domain%3Ayabot.jobs/searchAnalytics/query"
    assert body["startDate"] == (TODAY - timedelta(days=sc.BACKFILL_DAYS)).isoformat()
    assert body["endDate"] == TODAY.isoformat() and body["dimensions"] == ["date", "page"]
    assert auth == "Bearer t0k"
    stored = {(r.day, r.page): (r.clicks, r.impressions, r.position) for r in db.scalars(select(m.SearchPerformanceDay))}
    assert stored == {
        (date(2026, 10, 24), "jobs/us/software-engineer"): (4, 200, 10.0),  # position weighted by impressions
        (date(2026, 10, 25), "jobs/us/data-engineer"): (0, 40, 20.5),
    }


def test_later_runs_refresh_the_recent_days_only(db):
    old = TODAY - timedelta(days=30)
    db.add(m.SearchPerformanceDay(day=old, page="jobs/us/a", clicks=1, impressions=5, position=3.0))
    db.add(m.SearchPerformanceDay(day=TODAY - timedelta(days=1), page="jobs/us/a", clicks=1, impressions=5, position=3.0))
    db.commit()
    seen = []
    sc.ingest(db, NOW, token=lambda: "t", client=_client([[_row(str(TODAY), "https://yabot.jobs/jobs/us/a", 2, 9, 4.0)]], seen))
    assert seen[0][1]["startDate"] == (TODAY - timedelta(days=sc.REFRESH_DAYS)).isoformat()
    days = {r.day: r.clicks for r in db.scalars(select(m.SearchPerformanceDay))}
    assert days == {old: 1, TODAY: 2}  # the old day kept, the refreshed window replaced


def test_pages_through_long_results(db, monkeypatch):
    monkeypatch.setattr(sc, "ROW_LIMIT", 2)
    seen = []
    first = [_row("2026-10-25", f"https://yabot.jobs/p{i}", 0, 1, 1.0) for i in range(2)]
    second = [_row("2026-10-25", "https://yabot.jobs/p2", 0, 1, 1.0)]
    assert sc.ingest(db, NOW, token=lambda: "t", client=_client([first, second], seen)) == 3
    assert [body["startRow"] for _, body, _ in seen] == [0, 2]


def test_collection_totals_cover_its_pagination_not_its_children():
    totals = {
        "jobs/us/software-engineer": sc.PageTotals(2, 50),
        "jobs/us/software-engineer/page/2": sc.PageTotals(1, 10),
        "jobs/us/software-engineer/remote": sc.PageTotals(5, 99),
        "jobs/us/software-engineer-manager": sc.PageTotals(7, 70),
    }
    assert sc.collection_totals("jobs/us/software-engineer", totals) == sc.PageTotals(3, 60)


def test_rescoring_records_search_totals_without_scoring_them(db):
    c = collection(db, S.INDEXABLE, role="software-engineer")
    db.add(m.SearchPerformanceDay(day=TODAY - timedelta(days=2), page="jobs/us/software-engineer", clicks=4,
                                  impressions=120, position=9.0))
    db.add(m.SearchPerformanceDay(day=TODAY - timedelta(days=60), page="jobs/us/software-engineer", clicks=50,
                                  impressions=900, position=9.0))  # older than the window
    db.commit()
    cl.reevaluate_all(db, [job() for _ in range(60)], NOW, apply=True)
    assert c.qualification["search"] == {"clicks": 4, "impressions": 120, "days": sc.TOTALS_DAYS}
    assert c.qualification["demand_score"] is None  # report-only
