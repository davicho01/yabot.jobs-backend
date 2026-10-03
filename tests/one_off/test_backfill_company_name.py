"""one_off.backfill_company_name — same in-memory-SQLite pattern as
tests/one_off/test_backfill_country.py."""

import itertools
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import app.models as m
from app.db.base import Base
from one_off import backfill_company_name


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(_type, _compiler, **_kw):
    return "JSON"


@pytest.fixture
def session_factory(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine, tables=[m.CrawlSource.__table__, m.JobPostingUrl.__table__, m.JobPosting.__table__, m.Company.__table__]
    )
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(backfill_company_name, "SessionLocal", factory)
    return factory


_counter = itertools.count()


def _make_posting(
    session_factory, *, company_name: str | None, source_name: str | None
) -> str:
    n = next(_counter)
    db = session_factory()
    source = None
    if source_name is not None:
        source = m.CrawlSource(
            name=source_name, ats_type="workday", board_url=f"https://example.com/board/{n}"
        )
        db.add(source)
        db.flush()
    url_row = m.JobPostingUrl(
        url=f"https://example.com/job/{n}",
        normalized_url=f"https://example.com/job/{n}",
        url_hash=f"hash-{n}",
        domain="example.com",
        crawl_source_id=source.id if source is not None else None,
    )
    db.add(url_row)
    db.flush()
    posting = m.JobPosting(url_id=url_row.id, company_name=company_name)
    db.add(posting)
    db.commit()
    posting_id = posting.id
    db.close()
    return posting_id


def test_dry_run_reports_but_does_not_write(session_factory, monkeypatch):
    posting_id = _make_posting(session_factory, company_name=None, source_name="Capital One")
    monkeypatch.setattr(sys, "argv", ["backfill_company_name.py", "--dry-run"])

    backfill_company_name.main()

    db = session_factory()
    assert db.get(m.JobPosting, posting_id).company_name is None
    db.close()


def test_real_run_fills_in_company_name_from_crawl_source(session_factory, monkeypatch):
    posting_id = _make_posting(session_factory, company_name=None, source_name="Capital One")
    monkeypatch.setattr(sys, "argv", ["backfill_company_name.py"])

    backfill_company_name.main()

    db = session_factory()
    assert db.get(m.JobPosting, posting_id).company_name == "Capital One"
    db.close()


def test_existing_company_name_is_left_untouched(session_factory, monkeypatch):
    posting_id = _make_posting(session_factory, company_name="Real Scraped Co", source_name="Capital One")
    monkeypatch.setattr(sys, "argv", ["backfill_company_name.py"])

    backfill_company_name.main()

    db = session_factory()
    assert db.get(m.JobPosting, posting_id).company_name == "Real Scraped Co"
    db.close()


def test_user_submitted_posting_without_source_is_left_null(session_factory, monkeypatch):
    posting_id = _make_posting(session_factory, company_name=None, source_name=None)
    monkeypatch.setattr(sys, "argv", ["backfill_company_name.py"])

    backfill_company_name.main()

    db = session_factory()
    assert db.get(m.JobPosting, posting_id).company_name is None
    db.close()


def test_second_run_is_idempotent(session_factory, monkeypatch):
    posting_id = _make_posting(session_factory, company_name=None, source_name="Capital One")
    monkeypatch.setattr(sys, "argv", ["backfill_company_name.py"])
    backfill_company_name.main()

    backfill_company_name.main()  # second run: nothing left to change

    db = session_factory()
    assert db.get(m.JobPosting, posting_id).company_name == "Capital One"
    db.close()
