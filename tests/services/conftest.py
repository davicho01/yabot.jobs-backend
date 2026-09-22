"""Fixtures shared by this directory's plain-function (no FastAPI/HTTP)
service tests — the per-source scan-throttling ones and app.services.jobs's
own submission/dedup tests.

Like tests/test_oauth_service.py, these run against an in-memory SQLite
engine with only the tables they touch (JobPosting and friends use
Postgres-only JSONB). SQLite ignores `FOR UPDATE [SKIP LOCKED]`, so the
*locking* that serialises concurrent claims isn't exercised by the
scan-throttling tests — those cover the counting/claim logic that sits on
top of it.
"""

import itertools
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.db.base import Base
from app.models.enums import ScanStatus



@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(_type, _compiler, **_kw):
    # Lets JobPosting's table exist under SQLite so the real _upsert_posting
    # can run in these tests; only the column type differs, not the values.
    return "JSON"


NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def scan_db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[m.CrawlSource.__table__, m.JobPostingUrl.__table__, m.JobPosting.__table__, m.UserJobApplication.__table__],
    )
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture
def make_source(scan_db):
    counter = itertools.count()

    def _make(max_concurrent_scans: int = 3) -> m.CrawlSource:
        n = next(counter)
        source = m.CrawlSource(
            name=f"source-{n}",
            ats_type="greenhouse",
            board_url=f"https://boards.greenhouse.io/source-{n}",
            max_concurrent_scans=max_concurrent_scans,
        )
        scan_db.add(source)
        scan_db.commit()
        return source

    return _make


@pytest.fixture
def make_url(scan_db):
    counter = itertools.count()

    def _make(
        source: m.CrawlSource | None,
        *,
        status: str = ScanStatus.PENDING,
        claimed_ago: float | None = None,
        age_minutes: int | None = None,
        submitted_by_user_id: uuid.UUID | None = None,
    ) -> m.JobPostingUrl:
        """`claimed_ago`: seconds before NOW the row was claimed (None = unclaimed).
        `age_minutes`: how long before NOW it was created (controls oldest-first order)."""
        n = next(counter)
        row = m.JobPostingUrl(
            url=f"https://example.com/jobs/{n}",
            normalized_url=f"https://example.com/jobs/{n}",
            url_hash=f"hash-{n}",
            domain="example.com",
            scan_status=status,
            crawl_source_id=source.id if source is not None else None,
            scan_claimed_at=NOW - timedelta(seconds=claimed_ago) if claimed_ago is not None else None,
            created_at=NOW - timedelta(minutes=age_minutes if age_minutes is not None else n),
            submitted_by_user_id=submitted_by_user_id,
        )
        scan_db.add(row)
        scan_db.commit()
        return row

    return _make
