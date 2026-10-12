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
    return factory


_counter = itertools.count()


def _make_source(
    session_factory,
    *,
    status=CrawlSourceStatus.ACTIVE,
    crawl_claimed_at=None,
    is_official=True,
    next_crawl_at=None,
    last_crawled_at=None,
) -> str:
    n = next(_counter)
    db = session_factory()
    source = m.CrawlSource(
        name=f"Source {n}",
        ats_type="greenhouse",
        board_url=f"https://boards.greenhouse.io/source-{n}",
        status=status,
        crawl_claimed_at=crawl_claimed_at,
        is_official=is_official,
        next_crawl_at=next_crawl_at,
        last_crawled_at=last_crawled_at,
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


def test_a_job_board_source_is_never_dispatched(session_factory, monkeypatch):
    official = _make_source(session_factory)
    _make_source(session_factory, is_official=False)
    enqueued = []
    monkeypatch.setattr(crawl_dispatcher, "enqueue_crawl", lambda sid: enqueued.append(sid))

    crawl_dispatcher.main()

    assert enqueued == [official]


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
    _make_source(session_factory)
    monkeypatch.setattr(crawl_dispatcher, "enqueue_crawl", lambda sid: None)
    calls = []
    monkeypatch.setattr(crawl_dispatcher, "warm_up_browser_scaler", lambda: calls.append("called"))

    crawl_dispatcher.main()

    assert calls == ["called"]


def test_nothing_due_skips_the_warm_up(session_factory, monkeypatch):
    _make_source(session_factory, next_crawl_at=datetime.now(timezone.utc) + timedelta(hours=1))
    calls = []
    monkeypatch.setattr(crawl_dispatcher, "warm_up_browser_scaler", lambda: calls.append("called"))

    crawl_dispatcher.main()

    assert calls == []


def test_only_due_sources_are_dispatched(session_factory, monkeypatch):
    now = datetime.now(timezone.utc)
    never_scheduled = _make_source(session_factory)
    overdue = _make_source(session_factory, next_crawl_at=now - timedelta(minutes=5))
    _make_source(session_factory, next_crawl_at=now + timedelta(hours=1), last_crawled_at=now - timedelta(hours=1))
    enqueued = []
    monkeypatch.setattr(crawl_dispatcher, "enqueue_crawl", lambda sid: enqueued.append(sid))

    crawl_dispatcher.main()

    assert set(enqueued) == {never_scheduled, overdue}


def test_last_crawl_older_than_max_interval_is_due(session_factory, monkeypatch):
    # Lowering crawl_max_interval_minutes must take effect right away, not
    # after next_crawl_at values set under the old maximum.
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(crawl_dispatcher.settings, "crawl_max_interval_minutes", 1440)
    stale = _make_source(
        session_factory, next_crawl_at=now + timedelta(days=1), last_crawled_at=now - timedelta(days=2)
    )
    enqueued = []
    monkeypatch.setattr(crawl_dispatcher, "enqueue_crawl", lambda sid: enqueued.append(sid))

    crawl_dispatcher.main()

    assert enqueued == [stale]


def test_batch_cap_dispatches_most_overdue_first(session_factory, monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(crawl_dispatcher.settings, "crawl_dispatch_batch_size", 2)
    never_scheduled = _make_source(session_factory)
    oldest = _make_source(session_factory, next_crawl_at=now - timedelta(hours=3))
    _make_source(session_factory, next_crawl_at=now - timedelta(hours=1))
    enqueued = []
    monkeypatch.setattr(crawl_dispatcher, "enqueue_crawl", lambda sid: enqueued.append(sid))

    crawl_dispatcher.main()

    assert enqueued == [never_scheduled, oldest]
