"""crawl_dispatcher.py's claim-before-publish logic — the fix for a real
prod incident where a source with an outstanding, unprocessed wake-up got
another one piled on top of it every single dispatch cycle, building a
235,000-message backlog against ~2,800 active sources. Runs against an
in-memory SQLite engine; enqueue_crawl/wake_sources_with_pending_scans are
monkeypatched so no real Pub/Sub call happens, and the yabot-jobs-browser
warmup calls are monkeypatched so no real GCP API call happens.
"""

import itertools
import sys
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models as m
import crawl_dispatcher
from app.db.base import Base
from app.models.enums import CrawlSourceStatus

# crawl_claim_ttl_seconds defaults to 600: 100s ago is a live claim, 700s ago is abandoned.
_LIVE = 100
_EXPIRED = 700


@pytest.fixture
def session_factory(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.CrawlSource.__table__])
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(crawl_dispatcher, "SessionLocal", factory)
    monkeypatch.setattr(crawl_dispatcher, "ensure_topic", lambda: None)
    monkeypatch.setattr(crawl_dispatcher, "wake_sources_with_pending_scans", lambda db: 0)
    monkeypatch.setattr(crawl_dispatcher, "warm_up_browser_scaler", lambda: None)
    monkeypatch.setattr(sys, "argv", ["crawl_dispatcher.py"])
    # A 1h interval makes the per-run budget every active source, so the
    # claim tests below don't trip over it; the spreading tests set 12h.
    monkeypatch.setattr(crawl_dispatcher.settings, "crawl_interval_hours", 1.0)
    return factory


_counter = itertools.count()


def _make_source(
    session_factory, *, status=CrawlSourceStatus.ACTIVE, crawl_claimed_at=None, crawled_hours_ago=None
) -> str:
    n = next(_counter)
    db = session_factory()
    source = m.CrawlSource(
        name=f"Source {n}",
        ats_type="greenhouse",
        board_url=f"https://boards.greenhouse.io/source-{n}",
        status=status,
        crawl_claimed_at=crawl_claimed_at,
        last_crawled_at=(
            datetime.now(timezone.utc) - timedelta(hours=crawled_hours_ago) if crawled_hours_ago is not None else None
        ),
    )
    db.add(source)
    db.commit()
    source_id = source.id
    db.close()
    return source_id


def test_unclaimed_source_gets_dispatched_and_claimed(session_factory, monkeypatch):
    source_id = _make_source(session_factory)
    enqueued = []
    monkeypatch.setattr(crawl_dispatcher, "enqueue_crawl", lambda sid: enqueued.append(sid))

    crawl_dispatcher.main()

    assert enqueued == [source_id]
    db = session_factory()
    assert db.get(m.CrawlSource, source_id).crawl_claimed_at is not None
    db.close()


def test_live_claim_is_not_dispatched_again(session_factory, monkeypatch):
    # Regression: this is the exact bug — a source with an outstanding,
    # unprocessed wake-up used to get another one piled on top of it every
    # single dispatch cycle.
    claimed_at = datetime.now(timezone.utc) - timedelta(seconds=_LIVE)
    _make_source(session_factory, crawl_claimed_at=claimed_at)
    enqueued = []
    monkeypatch.setattr(crawl_dispatcher, "enqueue_crawl", lambda sid: enqueued.append(sid))

    crawl_dispatcher.main()

    assert enqueued == []


def test_expired_claim_is_dispatched_again(session_factory, monkeypatch):
    # A crawl-worker that died mid-crawl (or a lost message) shouldn't
    # strand a source unclaimed forever.
    claimed_at = datetime.now(timezone.utc) - timedelta(seconds=_EXPIRED)
    source_id = _make_source(session_factory, crawl_claimed_at=claimed_at)
    enqueued = []
    monkeypatch.setattr(crawl_dispatcher, "enqueue_crawl", lambda sid: enqueued.append(sid))

    crawl_dispatcher.main()

    assert enqueued == [source_id]


def test_inactive_sources_are_never_dispatched(session_factory, monkeypatch):
    _make_source(session_factory, status=CrawlSourceStatus.PENDING)
    _make_source(session_factory, status=CrawlSourceStatus.REJECTED)
    enqueued = []
    monkeypatch.setattr(crawl_dispatcher, "enqueue_crawl", lambda sid: enqueued.append(sid))

    crawl_dispatcher.main()

    assert enqueued == []


def test_claim_is_set_even_if_publishing_then_fails(session_factory, monkeypatch):
    # Claimed-before-publish, deliberately: a publish failure just waits out
    # the TTL, which is safer than the alternative (publish succeeds but the
    # claim commit is lost, letting the next cycle duplicate the message).
    source_id = _make_source(session_factory)

    def _boom(source_id):
        raise RuntimeError("pubsub unavailable")

    monkeypatch.setattr(crawl_dispatcher, "enqueue_crawl", _boom)

    crawl_dispatcher.main()  # must not raise — failures are caught per-source

    db = session_factory()
    assert db.get(m.CrawlSource, source_id).crawl_claimed_at is not None
    db.close()


def test_multiple_unclaimed_sources_all_get_dispatched(session_factory, monkeypatch):
    ids = {_make_source(session_factory) for _ in range(3)}
    enqueued = []
    monkeypatch.setattr(crawl_dispatcher, "enqueue_crawl", lambda sid: enqueued.append(sid))

    crawl_dispatcher.main()

    assert set(enqueued) == ids


def test_warms_the_browser_and_resumes_the_scaler_tick(session_factory, monkeypatch):
    calls = []
    monkeypatch.setattr(crawl_dispatcher, "warm_up_browser_scaler", lambda: calls.append("called"))

    crawl_dispatcher.main()

    assert calls == ["called"]


# --- Spreading crawls across the day: due-ness and the per-run budget -----


def _dispatch(session_factory, monkeypatch, interval_hours=12.0) -> list:
    monkeypatch.setattr(crawl_dispatcher.settings, "crawl_interval_hours", interval_hours)
    enqueued = []
    monkeypatch.setattr(crawl_dispatcher, "enqueue_crawl", lambda sid: enqueued.append(sid))
    crawl_dispatcher.main()
    return enqueued


def test_a_recently_crawled_source_is_not_due(session_factory, monkeypatch):
    _make_source(session_factory, crawled_hours_ago=2)
    assert _dispatch(session_factory, monkeypatch) == []


def test_a_source_crawled_an_interval_ago_is_due(session_factory, monkeypatch):
    source_id = _make_source(session_factory, crawled_hours_ago=12)
    assert _dispatch(session_factory, monkeypatch) == [source_id]


def test_due_slack_keeps_a_source_in_its_own_hour(session_factory, monkeypatch):
    # Crawled a few minutes into the hour 12h ago: due at the top of this hour, not the next.
    source_id = _make_source(session_factory, crawled_hours_ago=11.75)
    assert _dispatch(session_factory, monkeypatch) == [source_id]


def test_budget_is_one_hourly_share_of_active_sources(session_factory, monkeypatch):
    for _ in range(24):
        _make_source(session_factory, crawled_hours_ago=13)
    assert len(_dispatch(session_factory, monkeypatch)) == 2  # ceil(24 / 12)


def test_budget_counts_all_active_sources_not_just_due_ones(session_factory, monkeypatch):
    for _ in range(23):
        _make_source(session_factory, crawled_hours_ago=1)  # active, not due
    due = _make_source(session_factory, crawled_hours_ago=13)
    assert _dispatch(session_factory, monkeypatch) == [due]  # budget ceil(24/12)=2, only one due


def test_never_crawled_then_longest_waiting_go_first(session_factory, monkeypatch):
    for _ in range(20):
        _make_source(session_factory, crawled_hours_ago=1)  # pad active count: budget = ceil(24/12) = 2
    _make_source(session_factory, crawled_hours_ago=13)
    oldest = _make_source(session_factory, crawled_hours_ago=30)
    new = _make_source(session_factory)
    assert _dispatch(session_factory, monkeypatch) == [new, oldest]


def test_a_crowd_that_comes_due_together_is_dealt_out_over_successive_runs(session_factory, monkeypatch):
    ids = [_make_source(session_factory, crawled_hours_ago=13) for _ in range(36)]
    first = _dispatch(session_factory, monkeypatch)
    # The crawl worker clears the claim and stamps last_crawled_at once a crawl finishes.
    db = session_factory()
    for sid in first:
        source = db.get(m.CrawlSource, sid)
        source.crawl_claimed_at, source.last_crawled_at = None, datetime.now(timezone.utc)
    db.commit()
    db.close()
    second = _dispatch(session_factory, monkeypatch)

    assert len(first) == len(second) == 3  # ceil(36 / 12)
    assert not set(first) & set(second)
    assert set(first) | set(second) <= set(ids)

