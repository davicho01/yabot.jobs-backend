"""one_off.backfill_country — same in-memory-SQLite pattern as
tests/services/conftest.py (JSONB compiled to JSON so JobPosting's table can
exist under SQLite)."""

import itertools
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import app.models as m
from app.db.base import Base
from one_off import backfill_country


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(_type, _compiler, **_kw):
    return "JSON"


@pytest.fixture
def session_factory(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.JobPostingUrl.__table__, m.JobPosting.__table__, m.Company.__table__])
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(backfill_country, "SessionLocal", factory)
    return factory


_counter = itertools.count()


def _make_posting(session_factory, *, locations: list[str], country: str | None = None) -> str:
    n = next(_counter)
    db = session_factory()
    url_row = m.JobPostingUrl(
        url=f"https://example.com/job/{n}",
        normalized_url=f"https://example.com/job/{n}",
        url_hash=f"hash-{n}",
        domain="example.com",
    )
    db.add(url_row)
    db.flush()
    posting = m.JobPosting(url_id=url_row.id, locations=locations, country=country)
    db.add(posting)
    db.commit()
    posting_id = posting.id
    db.close()
    return posting_id


def test_dry_run_reports_but_does_not_write(session_factory, monkeypatch):
    posting_id = _make_posting(session_factory, locations=["Austin, TX"])
    monkeypatch.setattr(sys, "argv", ["backfill_country.py", "--dry-run"])

    backfill_country.main()

    db = session_factory()
    assert db.get(m.JobPosting, posting_id).country is None
    db.close()


def test_real_run_writes_resolved_country(session_factory, monkeypatch):
    _make_posting(session_factory, locations=["Austin, TX"])
    _make_posting(session_factory, locations=["Toronto, Canada"])
    _make_posting(session_factory, locations=["Remote"])
    monkeypatch.setattr(sys, "argv", ["backfill_country.py"])

    backfill_country.main()

    db = session_factory()
    countries = sorted((p.country for p in db.query(m.JobPosting).all()), key=lambda c: c or "")
    assert countries == [None, "CA", "US"]
    db.close()


def test_second_run_is_idempotent(session_factory, monkeypatch):
    posting_id = _make_posting(session_factory, locations=["Austin, TX"])
    monkeypatch.setattr(sys, "argv", ["backfill_country.py"])
    backfill_country.main()

    db = session_factory()
    first_country = db.get(m.JobPosting, posting_id).country
    db.close()

    backfill_country.main()  # second run: nothing left to change

    db = session_factory()
    assert db.get(m.JobPosting, posting_id).country == first_country == "US"
    db.close()
