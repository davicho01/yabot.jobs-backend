"""one_off.mark_incomplete_scans — same in-memory-SQLite pattern as
tests/one_off/test_backfill_description_entities.py."""

import itertools
import sys
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import app.models as m
from app.db.base import Base
from app.models.enums import ScanStatus
from app.services.scan_completeness import INCOMPLETE_SCAN_ERROR_PREFIX
from one_off import mark_incomplete_scans

FULL = "We are hiring. " * 30


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(_type, _compiler, **_kw):
    return "JSON"


@pytest.fixture
def session_factory(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine, tables=[m.JobPostingUrl.__table__, m.JobPosting.__table__, m.Company.__table__, m.CrawlSource.__table__]
    )
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(mark_incomplete_scans, "SessionLocal", factory)
    monkeypatch.setattr(mark_incomplete_scans, "BATCH_SIZE", 2)  # exercise the batching
    return factory


_counter = itertools.count()


def _job(factory, description, *, domain="example.com", scan_status=ScanStatus.SUCCESS, closed=False):
    n = next(_counter)
    db = factory()
    url_row = m.JobPostingUrl(
        url=f"https://{domain}/job/{n}",
        normalized_url=f"https://{domain}/job/{n}",
        url_hash=f"mark-hash-{n}",
        domain=domain,
        scan_status=scan_status,
        closed_at=datetime.now(timezone.utc) if closed else None,
    )
    db.add(url_row)
    db.flush()
    db.add(m.JobPosting(url_id=url_row.id, description=description, extraction_status=ScanStatus.SUCCESS))
    db.commit()
    url_id = url_row.id
    db.close()
    return url_id


def _run(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["mark_incomplete_scans.py", *args])
    mark_incomplete_scans.main()


def _state(factory, url_id):
    db = factory()
    url_row = db.get(m.JobPostingUrl, url_id)
    posting = db.query(m.JobPosting).filter_by(url_id=url_id).one()
    state = (url_row.scan_status, url_row.scan_attempts, url_row.next_retry_at, url_row.scan_error, posting.extraction_status)
    db.close()
    return state


def test_marks_only_open_successful_short_jobs(session_factory, monkeypatch):
    short = _job(session_factory, "Durham, NC")
    empty = _job(session_factory, None)
    full = _job(session_factory, FULL)
    closed = _job(session_factory, "Durham, NC", closed=True)
    pending = _job(session_factory, "Durham, NC", scan_status=ScanStatus.PENDING)

    _run(monkeypatch)

    for url_id in (short, empty):
        status, attempts, next_retry_at, error, posting_status = _state(session_factory, url_id)
        assert (status, attempts, posting_status) == (ScanStatus.FAILED, 1, ScanStatus.FAILED)
        assert next_retry_at is not None and error.startswith(INCOMPLETE_SCAN_ERROR_PREFIX)
    for url_id in (full, closed, pending):
        assert _state(session_factory, url_id)[4] == ScanStatus.SUCCESS  # untouched


def test_retries_are_spread_over_the_window(session_factory, monkeypatch):
    ids = [_job(session_factory, "teaser") for _ in range(20)]
    before = datetime.now(timezone.utc).replace(tzinfo=None)

    _run(monkeypatch, "--spread-hours", "24")

    retry_times = [_state(session_factory, i)[2].replace(tzinfo=None) for i in ids]
    assert all(before <= t <= before + timedelta(hours=24, minutes=1) for t in retry_times)
    assert max(retry_times) - min(retry_times) > timedelta(hours=1)  # not all at once


def test_dry_run_changes_nothing(session_factory, monkeypatch):
    url_id = _job(session_factory, "teaser")

    _run(monkeypatch, "--dry-run")

    assert _state(session_factory, url_id)[0] == ScanStatus.SUCCESS


def test_limit_and_domain(session_factory, monkeypatch):
    workable = [_job(session_factory, "teaser", domain="apply.workable.com") for _ in range(5)]
    other = _job(session_factory, "teaser", domain="boards.greenhouse.io")

    _run(monkeypatch, "--domain", "apply.workable.com", "--limit", "3")

    marked = [i for i in workable if _state(session_factory, i)[0] == ScanStatus.FAILED]
    assert len(marked) == 3
    assert _state(session_factory, other)[0] == ScanStatus.SUCCESS
