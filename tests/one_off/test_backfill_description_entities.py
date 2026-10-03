"""one_off.backfill_description_entities — same in-memory-SQLite pattern as
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
from one_off import backfill_description_entities


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(_type, _compiler, **_kw):
    return "JSON"


@pytest.fixture
def session_factory(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.JobPostingUrl.__table__, m.JobPosting.__table__, m.Company.__table__])
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(backfill_description_entities, "SessionLocal", factory)
    return factory


_counter = itertools.count()


def _make_posting(session_factory, *, description: str | None) -> str:
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
    posting = m.JobPosting(url_id=url_row.id, description=description)
    db.add(posting)
    db.commit()
    posting_id = posting.id
    db.close()
    return posting_id


def test_dry_run_reports_but_does_not_write(session_factory, monkeypatch):
    posting_id = _make_posting(session_factory, description="Date Posted:&#xa;2026-09-27")
    monkeypatch.setattr(sys, "argv", ["backfill_description_entities.py", "--dry-run"])

    backfill_description_entities.main()

    db = session_factory()
    assert db.get(m.JobPosting, posting_id).description == "Date Posted:&#xa;2026-09-27"
    db.close()


def test_real_run_decodes_entities(session_factory, monkeypatch):
    posting_id = _make_posting(session_factory, description="Date Posted:&#xa;2026-09-27")
    clean_id = _make_posting(session_factory, description="already clean")
    none_id = _make_posting(session_factory, description=None)
    monkeypatch.setattr(sys, "argv", ["backfill_description_entities.py"])

    backfill_description_entities.main()

    db = session_factory()
    assert db.get(m.JobPosting, posting_id).description == "Date Posted:\n2026-09-27"
    assert db.get(m.JobPosting, clean_id).description == "already clean"
    assert db.get(m.JobPosting, none_id).description is None
    db.close()


def test_second_run_is_idempotent(session_factory, monkeypatch):
    posting_id = _make_posting(session_factory, description="Date Posted:&#xa;2026-09-27")
    monkeypatch.setattr(sys, "argv", ["backfill_description_entities.py"])
    backfill_description_entities.main()

    db = session_factory()
    first = db.get(m.JobPosting, posting_id).description
    db.close()

    backfill_description_entities.main()  # second run: nothing left to change

    db = session_factory()
    assert db.get(m.JobPosting, posting_id).description == first == "Date Posted:\n2026-09-27"
    db.close()
