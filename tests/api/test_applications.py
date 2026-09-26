import itertools
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes.applications import bulk_update_applications, update_application
from app.db.base import Base
from app.models.enums import ScanStatus
from app.models.user import User
from app.schemas.application import ApplicationBulkUpdate, ApplicationUpdate

_url_counter = itertools.count()


@pytest.fixture
def db() -> Session:
    # Local db fixture (not tests/api/conftest.py's, which only has
    # SavedSearch) — applications need JobPostingUrl/JobPosting too, for
    # UserJobApplication's FK. Resume is needed too, for selected_resume_id.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[m.JobPostingUrl.__table__, m.JobPosting.__table__, m.UserJobApplication.__table__, m.Resume.__table__],
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


def test_update_application_marking_applied_sets_applied_at(db):
    user = _user()
    application = _make_application(db, user)
    assert application.applied_at is None

    result = update_application(
        application.id, ApplicationUpdate(status="applied"), current_user=user, db=db
    )

    assert result.applied_at is not None


def test_update_application_sets_selected_resume_id(db):
    user = _user()
    application = _make_application(db, user)
    resume_id = uuid.uuid4()
    resume = m.Resume(
        id=resume_id,
        user_id=user.id,
        filename="resume.pdf",
        content_type="application/pdf",
        storage_key="k",
        parsed_text="x",
        root_resume_id=resume_id,
    )
    db.add(resume)
    db.flush()

    result = update_application(
        application.id, ApplicationUpdate(selected_resume_id=resume.id), current_user=user, db=db
    )

    assert result.selected_resume_id == resume.id


def test_update_application_rejects_another_users_resume_as_selected_resume_id(db):
    user = _user()
    other_user = _user()
    application = _make_application(db, user)
    other_resume_id = uuid.uuid4()
    other_resume = m.Resume(
        id=other_resume_id,
        user_id=other_user.id,
        filename="resume.pdf",
        content_type="application/pdf",
        storage_key="k",
        parsed_text="x",
        root_resume_id=other_resume_id,
    )
    db.add(other_resume)
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        update_application(
            application.id, ApplicationUpdate(selected_resume_id=other_resume.id), current_user=user, db=db
        )
    assert exc_info.value.status_code == 404


def test_update_application_clearing_selected_resume_id_is_distinct_from_omitting_it(db):
    user = _user()
    application = _make_application(db, user)
    resume_id = uuid.uuid4()
    resume = m.Resume(
        id=resume_id,
        user_id=user.id,
        filename="resume.pdf",
        content_type="application/pdf",
        storage_key="k",
        parsed_text="x",
        root_resume_id=resume_id,
    )
    db.add(resume)
    db.flush()
    application.selected_resume_id = resume.id
    db.flush()

    result = update_application(
        application.id, ApplicationUpdate(selected_resume_id=None), current_user=user, db=db
    )
    assert result.selected_resume_id is None

    application.selected_resume_id = resume.id
    db.flush()
    result = update_application(application.id, ApplicationUpdate(notes="just notes"), current_user=user, db=db)
    assert result.selected_resume_id == resume.id


def test_update_application_404s_for_an_unknown_id(db):
    with pytest.raises(HTTPException) as exc_info:
        update_application(uuid.uuid4(), ApplicationUpdate(notes="x"), current_user=_user(), db=db)
    assert exc_info.value.status_code == 404


# ------------------------------------------------------------ bulk update


def test_bulk_update_applications_sets_status_on_every_matching_id(db):
    user = _user()
    a = _make_application(db, user)
    b = _make_application(db, user)

    result = bulk_update_applications(
        ApplicationBulkUpdate(ids=[a.id, b.id], status="interviewing"), current_user=user, db=db
    )

    assert {r.id for r in result} == {a.id, b.id}
    assert a.status == "interviewing"
    assert b.status == "interviewing"


def test_bulk_update_applications_marking_applied_sets_applied_at(db):
    user = _user()
    a = _make_application(db, user)
    assert a.applied_at is None

    bulk_update_applications(ApplicationBulkUpdate(ids=[a.id], status="applied"), current_user=user, db=db)

    assert a.applied_at is not None


def test_bulk_update_applications_sets_is_archived_on_every_matching_id(db):
    user = _user()
    a = _make_application(db, user)
    b = _make_application(db, user)

    result = bulk_update_applications(
        ApplicationBulkUpdate(ids=[a.id, b.id], is_archived=True), current_user=user, db=db
    )

    assert {r.id for r in result} == {a.id, b.id}
    assert a.is_archived is True
    assert b.is_archived is True
    # status untouched — not part of this call
    assert a.status == "saved"


def test_bulk_update_applications_can_set_status_and_is_archived_together(db):
    user = _user()
    a = _make_application(db, user)

    bulk_update_applications(
        ApplicationBulkUpdate(ids=[a.id], status="withdrawn", is_archived=True), current_user=user, db=db
    )

    assert a.status == "withdrawn"
    assert a.is_archived is True


def test_bulk_update_applications_silently_skips_ids_owned_by_someone_else(db):
    owner = _user()
    other_user = _user()
    mine = _make_application(db, owner)
    theirs = _make_application(db, other_user)

    result = bulk_update_applications(
        ApplicationBulkUpdate(ids=[mine.id, theirs.id], status="rejected"), current_user=owner, db=db
    )

    assert [r.id for r in result] == [mine.id]
    assert mine.status == "rejected"
    assert theirs.status == "saved"  # untouched — not this user's row


def test_bulk_update_applications_silently_skips_unknown_ids(db):
    user = _user()
    a = _make_application(db, user)

    result = bulk_update_applications(
        ApplicationBulkUpdate(ids=[a.id, uuid.uuid4()], status="withdrawn"), current_user=user, db=db
    )

    assert [r.id for r in result] == [a.id]
    assert a.status == "withdrawn"


def test_bulk_update_applications_with_empty_ids_is_a_no_op(db):
    result = bulk_update_applications(ApplicationBulkUpdate(ids=[], status="applied"), current_user=_user(), db=db)
    assert result == []
