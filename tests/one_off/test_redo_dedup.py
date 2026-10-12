"""one_off.redo_dedup — regroup() on its own, then main() against in-memory
SQLite (same pattern as test_backfill_country)."""

import itertools
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import app.models as m
from app.db.base import Base
from app.models.enums import CrawlSourceStatus, ScanStatus
from one_off import redo_dedup
from one_off.redo_dedup import Row, regroup


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(_type, _compiler, **_kw):
    return "JSON"


T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
_counter = itertools.count()


def _row(title="cashier", location="Store 1", *, official=False, closed=False, age=0, primary=None,
         success=True, domain="acme.com") -> Row:
    return Row(
        id=uuid.uuid4(),
        company_key="acme",
        title_key=title,
        success=success,
        official=official,
        closed=closed,
        created_at=T0 - timedelta(days=age),
        primary_posting_id=primary,
        domain=domain,
        locations=[location] if location else [],
        places=[],
    )


# ------------------------------------------------------------------ regroup


def test_same_store_repeats_merge_under_the_oldest():
    old, new = _row(age=5), _row(age=1)
    assert regroup([new, old]) == {old.id: None, new.id: old.id}


def test_different_stores_and_titles_stay_apart():
    a, b, c = _row(location="Store 1"), _row(location="Store 2"), _row(title="cashier part time")
    assert regroup([a, b, c]) == {a.id: None, b.id: None, c.id: None}


def test_an_old_wrong_link_is_undone():
    primary = _row(location="Store 1", age=5)
    wrongly_linked = _row(location="Store 2", primary=primary.id)
    assert regroup([primary, wrongly_linked])[wrongly_linked.id] is None


def test_primary_preference_official_then_open_then_current():
    oldest = _row(age=9)
    current = _row(age=1)
    oldest.primary_posting_id = current.id
    assert regroup([oldest, current])[oldest.id] == current.id  # current primary keeps it

    closed = _row(age=20, closed=True)
    assert regroup([closed, current])[closed.id] == current.id  # open beats closed

    official = _row(age=0, official=True, primary=current.id)
    assert regroup([official, current])[current.id] == official.id  # official beats all


def test_failed_rows_follow_their_group_to_its_new_primary():
    official = _row(official=True)
    old_primary = _row(age=5)
    failed = _row(success=False, primary=old_primary.id)
    result = regroup([official, old_primary, failed])
    assert result[old_primary.id] == official.id
    assert result[failed.id] == official.id


# --------------------------------------------------------------------- main


@pytest.fixture
def session_factory(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine, tables=[m.CrawlSource.__table__, m.JobPostingUrl.__table__, m.JobPosting.__table__, m.Company.__table__]
    )
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(redo_dedup, "SessionLocal", factory)
    return factory


def _make_posting(factory, *, location, primary_id=None, age=0) -> uuid.UUID:
    n = next(_counter)
    db = factory()
    url_row = m.JobPostingUrl(
        url=f"https://acme.com/job/{n}",
        normalized_url=f"https://acme.com/job/{n}",
        url_hash=f"redo-hash-{n}",
        domain="acme.com",
        created_at=T0 - timedelta(days=age),
    )
    db.add(url_row)
    db.flush()
    posting = m.JobPosting(
        url_id=url_row.id,
        title="Cashier",
        company_name="Acme",
        company_key="acme",
        title_key="cashier",
        location=location,
        locations=[location],
        extraction_status=ScanStatus.SUCCESS,
        primary_posting_id=primary_id,
    )
    db.add(posting)
    db.commit()
    posting_id = posting.id
    db.close()
    return posting_id


def _primary_of(factory, posting_id):
    db = factory()
    try:
        return db.get(m.JobPosting, posting_id).primary_posting_id
    finally:
        db.close()


def _run(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["redo_dedup", *args])
    redo_dedup.main()


def test_main_fixes_links_and_is_idempotent(session_factory, monkeypatch):
    store_1 = _make_posting(session_factory, location="Store 1", age=5)
    wrong = _make_posting(session_factory, location="Store 2", primary_id=store_1)
    repeat = _make_posting(session_factory, location="Store 1", age=1)

    _run(monkeypatch)
    assert _primary_of(session_factory, wrong) is None
    assert _primary_of(session_factory, repeat) == store_1
    assert _primary_of(session_factory, store_1) is None

    _run(monkeypatch)  # a second run changes nothing
    assert _primary_of(session_factory, repeat) == store_1
    assert _primary_of(session_factory, wrong) is None


def test_dry_run_writes_nothing(session_factory, monkeypatch):
    store_1 = _make_posting(session_factory, location="Store 1", age=5)
    wrong = _make_posting(session_factory, location="Store 2", primary_id=store_1)

    _run(monkeypatch, "--dry-run")
    assert _primary_of(session_factory, wrong) == store_1


@pytest.mark.parametrize("status, official_wins", [(CrawlSourceStatus.ACTIVE, True), (CrawlSourceStatus.PENDING, False)])
def test_an_active_official_source_takes_over_as_primary(session_factory, monkeypatch, status, official_wins):
    board_copy = _make_posting(session_factory, location="Store 1", age=5)
    official_copy = _make_posting(session_factory, location="Store 1", primary_id=board_copy)
    db = session_factory()
    source = m.CrawlSource(name="Acme", ats_type="greenhouse", board_url="https://acme.com/jobs", is_official=True,
                           status=status)
    db.add(source)
    db.flush()
    db.get(m.JobPosting, official_copy).url.crawl_source_id = source.id
    db.commit()
    db.close()

    _run(monkeypatch)
    if official_wins:
        assert _primary_of(session_factory, official_copy) is None
        assert _primary_of(session_factory, board_copy) == official_copy
    else:
        assert _primary_of(session_factory, official_copy) == board_copy
