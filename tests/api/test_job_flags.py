import itertools
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes.admin import dismiss_listing_flag, get_jobs
from app.api.routes.jobs import flag_job
from app.db.base import Base
from app.models.enums import FlagReason
from app.models.user import User
from app.schemas.job import JobFlagCreate

_url_counter = itertools.count()


@pytest.fixture
def db() -> Session:
    # Local db fixture (not tests/api/conftest.py's, which only has
    # SavedSearch) — these routes need JobPostingUrl/JobPosting/CrawlSource
    # (get_jobs joins/filters on CrawlSource for source_id).
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.JobPostingUrl.__table__, m.JobPosting.__table__, m.Company.__table__, m.CrawlSource.__table__])
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


def _user() -> User:
    # Never persisted — flag_job only ever reads current_user.id.
    return User(id=uuid.uuid4(), email=f"{uuid.uuid4()}@example.com")


def _make_url(db) -> m.JobPostingUrl:
    n = next(_url_counter)
    url_row = m.JobPostingUrl(
        url=f"https://example.com/jobs/flag-{n}",
        normalized_url=f"https://example.com/jobs/flag-{n}",
        url_hash=f"flag-hash-{n}",
        domain="example.com",
    )
    db.add(url_row)
    db.commit()
    return url_row


def test_flag_job_returns_404_for_an_unknown_url_id(db):
    with pytest.raises(HTTPException) as exc_info:
        flag_job(uuid.uuid4(), JobFlagCreate(reason=FlagReason.OTHER), current_user=_user(), db=db)
    assert exc_info.value.status_code == 404


def test_flag_job_records_the_report_but_does_not_echo_it_back(db):
    url_row = _make_url(db)
    reporter = _user()
    payload = JobFlagCreate(reason=FlagReason.WRONG_DETAILS, note="location says Remote but it's onsite")

    detail = flag_job(url_row.id, payload, current_user=reporter, db=db)

    # Stored on the row...
    assert url_row.flag_reason == FlagReason.WRONG_DETAILS
    assert url_row.flag_note == "location says Remote but it's onsite"
    assert url_row.flagged_by_user_id == reporter.id
    assert url_row.flagged_at is not None
    # ...but not surfaced back to the reporter (include_flag defaults False).
    assert detail.url.flagged_at is None
    assert detail.url.flag_reason is None
    assert detail.url.flag_note is None


def test_admin_get_jobs_flagged_filter_scopes_to_reported_listings(db):
    flagged_row = _make_url(db)
    flag_job(flagged_row.id, JobFlagCreate(reason=FlagReason.OTHER), current_user=_user(), db=db)
    _make_url(db)  # not flagged

    result = get_jobs(
        source_id=None,
        scan_from=None,
        scan_to=None,
        flagged=True,
        page=1,
        page_size=20,
        sort_by="discovered",
        sort_order="desc",
        db=db,
    )

    assert [item.url.id for item in result.items] == [flagged_row.id]
    # Admin listing shows the report (include_flag=True), unlike the
    # reporter-facing response above.
    assert result.items[0].url.flag_reason == FlagReason.OTHER


def test_admin_get_jobs_unflagged_filter_excludes_reported_listings(db):
    flagged_row = _make_url(db)
    flag_job(flagged_row.id, JobFlagCreate(reason=FlagReason.OTHER), current_user=_user(), db=db)
    clean_row = _make_url(db)

    result = get_jobs(
        source_id=None,
        scan_from=None,
        scan_to=None,
        flagged=False,
        page=1,
        page_size=20,
        sort_by="discovered",
        sort_order="desc",
        db=db,
    )

    assert [item.url.id for item in result.items] == [clean_row.id]


def test_dismiss_listing_flag_returns_404_for_an_unknown_url_id(db):
    with pytest.raises(HTTPException) as exc_info:
        dismiss_listing_flag(uuid.uuid4(), db=db)
    assert exc_info.value.status_code == 404


def test_dismiss_listing_flag_clears_the_report(db):
    url_row = _make_url(db)
    flag_job(url_row.id, JobFlagCreate(reason=FlagReason.BROKEN_OR_EXPIRED), current_user=_user(), db=db)

    detail = dismiss_listing_flag(url_row.id, db=db)

    assert url_row.flagged_at is None
    assert url_row.flag_reason is None
    assert url_row.flagged_by_user_id is None
    assert detail.url.flagged_at is None
