import itertools
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes.admin import get_incomplete_scans_by_domain, get_jobs
from app.db.base import Base
from app.services.scan_completeness import INCOMPLETE_SCAN_ERROR_PREFIX

_counter = itertools.count()


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine, tables=[m.JobPostingUrl.__table__, m.JobPosting.__table__, m.Company.__table__, m.CrawlSource.__table__]
    )
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


def _url(db, domain="example.com", scan_error=None, closed=False) -> m.JobPostingUrl:
    n = next(_counter)
    row = m.JobPostingUrl(
        url=f"https://{domain}/jobs/{n}",
        normalized_url=f"https://{domain}/jobs/{n}",
        url_hash=f"incomplete-hash-{n}",
        domain=domain,
        scan_error=scan_error,
        closed_at=datetime.now(timezone.utc) if closed else None,
    )
    db.add(row)
    db.commit()
    return row


INCOMPLETE = f"{INCOMPLETE_SCAN_ERROR_PREFIX}no description"


def _jobs(db, incomplete):
    return get_jobs(
        source_id=None,
        scan_from=None,
        scan_to=None,
        flagged=None,
        incomplete=incomplete,
        page=1,
        page_size=20,
        sort_by="discovered",
        sort_order="desc",
        db=db,
    )


def test_incomplete_filter_lists_only_incomplete_scans(db):
    incomplete = _url(db, scan_error=INCOMPLETE)
    blocked = _url(db, scan_error="403 Forbidden")
    clean = _url(db)

    assert [i.url.id for i in _jobs(db, True).items] == [incomplete.id]
    assert {i.url.id for i in _jobs(db, False).items} == {blocked.id, clean.id}


def test_counts_incomplete_open_listings_by_domain(db):
    for _ in range(3):
        _url(db, "apply.workable.com", INCOMPLETE)
    _url(db, "boards.greenhouse.io", INCOMPLETE)
    _url(db, "boards.greenhouse.io", INCOMPLETE, closed=True)  # closed: not counted
    _url(db, "boards.greenhouse.io", "403 Forbidden")  # other failure: not counted

    assert get_incomplete_scans_by_domain(limit=50, db=db) == [
        {"domain": "apply.workable.com", "count": 3},
        {"domain": "boards.greenhouse.io", "count": 1},
    ]
