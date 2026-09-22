import itertools
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes.applications import update_application
from app.db.base import Base
from app.models.enums import ScanStatus
from app.models.user import User
from app.schemas.application import ApplicationUpdate

_url_counter = itertools.count()


@pytest.fixture
def db() -> Session:
    # Local db fixture (not tests/api/conftest.py's, which only has
    # SavedSearch) — applications need JobPostingUrl/JobPosting too, for
    # UserJobApplication's FK.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine, tables=[m.JobPostingUrl.__table__, m.JobPosting.__table__, m.UserJobApplication.__table__]
    )
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


def _user() -> User:
    return User(id=uuid.uuid4(), email="person@example.com")


def _make_application(db, user: User) -> m.UserJobApplication:
    n = next(_url_counter)
    url = m.JobPostingUrl(
        url=f"https://example.com/jobs/{n}", normalized_url=f"https://example.com/jobs/{n}", url_hash=f"h{n}", domain="example.com"
    )
    db.add(url)
    db.flush()
    posting = m.JobPosting(url_id=url.id, title="Engineer", extraction_status=ScanStatus.SUCCESS)
    db.add(posting)
    db.flush()
    application = m.UserJobApplication(user_id=user.id, job_posting_id=posting.id, status="saved")
    db.add(application)
    db.flush()
    return application


def test_update_application_only_touches_fields_actually_sent(db):
    user = _user()
    application = _make_application(db, user)
    application.notes = "Original notes"
    db.flush()

    result = update_application(application.id, ApplicationUpdate(is_archived=True), current_user=user, db=db)

    assert result.is_archived is True
    assert result.notes == "Original notes"  # untouched, matching the earlier UserUpdate bug fix


def test_update_application_setting_follow_up_at_resets_the_reminded_flag(db):
    user = _user()
    application = _make_application(db, user)
    application.follow_up_reminded_at = datetime.now(timezone.utc)  # pretend one was already sent for some prior date
    db.flush()

    new_date = date.today() + timedelta(days=3)
    result = update_application(application.id, ApplicationUpdate(follow_up_at=new_date), current_user=user, db=db)

    assert result.follow_up_at == new_date
    assert application.follow_up_reminded_at is None


def test_update_application_clearing_follow_up_at_is_distinct_from_omitting_it(db):
    user = _user()
    application = _make_application(db, user)
    application.follow_up_at = date.today()
    db.flush()

    # Explicitly sent as null — a deliberate clear.
    result = update_application(application.id, ApplicationUpdate(follow_up_at=None), current_user=user, db=db)
    assert result.follow_up_at is None

    # Omitted entirely — leaves it alone (still null from above, but the
    # point is this path doesn't even look at follow_up_at).
    application.follow_up_at = date.today()
    db.flush()
    result = update_application(application.id, ApplicationUpdate(notes="just notes"), current_user=user, db=db)
    assert result.follow_up_at == date.today()


def test_update_application_resaving_the_same_follow_up_at_does_not_reset_the_reminded_flag(db):
    user = _user()
    application = _make_application(db, user)
    today = date.today()
    reminded_at = datetime.now(timezone.utc)
    application.follow_up_at = today
    application.follow_up_reminded_at = reminded_at
    db.flush()

    update_application(application.id, ApplicationUpdate(follow_up_at=today), current_user=user, db=db)

    assert application.follow_up_reminded_at == reminded_at  # unchanged — same date, no new reminder needed


def test_update_application_404s_for_an_unknown_id(db):
    with pytest.raises(HTTPException) as exc_info:
        update_application(uuid.uuid4(), ApplicationUpdate(notes="x"), current_user=_user(), db=db)
    assert exc_info.value.status_code == 404
