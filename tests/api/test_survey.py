import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes import survey as survey_routes
from app.core.config import settings
from app.db.base import Base
from app.schemas.survey import SurveySubmit


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.User.__table__, m.SurveyResponse.__table__])
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


def _make_user(db) -> m.User:
    user = m.User(id=uuid.uuid4(), email=f"{uuid.uuid4()}@example.com")
    db.add(user)
    db.commit()
    return user


def _submit(db, user=None, **fields):
    fields.setdefault("visitor_id", "visitor-abc12345")
    return survey_routes.submit_survey(SurveySubmit(**fields), current_user=user, db=db)


def test_anonymous_visitor_can_submit_page_one(db):
    response = _submit(db, source="google", search_duration="1_3m", industries=["tech", "legal"], role_name="  ICU nurse ")

    assert response.completed is False
    row = db.query(m.SurveyResponse).one()
    assert row.user_id is None
    assert row.source == "google"
    assert row.industries == ["tech", "legal"]
    assert row.role_name == "ICU nurse"


def test_page_two_updates_same_row_and_keeps_page_one(db):
    _submit(db, source="reddit", work_arrangement=["remote"])
    _submit(db, completed=True, dedup_rating="very_useful", liked=["fresh_jobs"], contact_email="a@b.co")

    row = db.query(m.SurveyResponse).one()
    assert row.completed is True
    assert row.source == "reddit"
    assert row.work_arrangement == ["remote"]
    assert row.dedup_rating == "very_useful"
    assert row.contact_email == "a@b.co"
    # Leaving an email is the consent for a signed-out visitor.
    assert row.contact_ok is True


def test_new_questions_are_saved_and_counted(db):
    _submit(
        db,
        industries=["other"],
        industry_other="  Aerospace ",
        current_tools=["linkedin", "indeed"],
        frustration="Ghost jobs",
    )
    _submit(
        db,
        visitor_id="visitor-00000002",
        completed=True,
        posted_date_rating="not_useful",
        first_found_date_rating="didnt_notice",
        verified_date_rating="useful",
    )

    row = db.query(m.SurveyResponse).filter_by(visitor_id="visitor-abc12345").one()
    assert row.industry_other == "Aerospace"
    assert row.current_tools == ["linkedin", "indeed"]
    assert row.frustration == "Ghost jobs"
    counts = survey_routes.list_survey_responses(completed_only=False, page=1, page_size=20, db=db).counts
    assert counts["current_tools"] == {"linkedin": 1, "indeed": 1}
    assert counts["verified_date_rating"] == {"useful": 1}
    assert counts["posted_date_rating"] == {"not_useful": 1}


def test_unknown_date_rating_is_rejected():
    with pytest.raises(ValidationError):
        SurveySubmit(visitor_id="visitor-abc12345", posted_date_rating="very_useful")


def test_signed_out_without_email_is_not_contactable(db):
    _submit(db, completed=True, contact_ok=True)

    assert db.query(m.SurveyResponse).one().contact_ok is False


def test_signed_in_user_is_linked_and_typed_email_is_dropped(db):
    user = _make_user(db)

    _submit(db, user=user, completed=True, contact_email="typed@example.com")

    row = db.query(m.SurveyResponse).one()
    assert row.user_id == user.id
    assert row.contact_email is None
    assert row.contact_ok is False


def test_signed_in_user_can_agree_to_be_contacted(db):
    user = _make_user(db)

    _submit(db, user=user, completed=True, contact_ok=True)

    assert db.query(m.SurveyResponse).one().contact_ok is True


def test_finished_response_stays_finished(db):
    _submit(db, completed=True)
    _submit(db, source="friend")

    assert db.query(m.SurveyResponse).one().completed is True


def test_unknown_answers_are_rejected():
    with pytest.raises(ValidationError):
        SurveySubmit(visitor_id="visitor-abc12345", source="carrier-pigeon")
    with pytest.raises(ValidationError):
        SurveySubmit(visitor_id="visitor-abc12345", industries=["tech", "legal", "retail", "education"])
    with pytest.raises(ValidationError):
        SurveySubmit(visitor_id="visitor-abc12345", contact_email="not-an-email")


def test_new_response_rate_limit_only_counts_new_rows(db, monkeypatch):
    monkeypatch.setattr(settings, "survey_rate_limit_max_new", 2)
    _submit(db, visitor_id="visitor-00000001")
    _submit(db, visitor_id="visitor-00000002")

    # Editing an existing response is still fine.
    _submit(db, visitor_id="visitor-00000001", source="google")
    with pytest.raises(HTTPException) as exc:
        _submit(db, visitor_id="visitor-00000003")
    assert exc.value.status_code == 429


def test_admin_list_has_user_email_and_counts(db):
    user = _make_user(db)
    _submit(db, user=user, visitor_id="visitor-00000001", completed=True, source="google", industries=["tech"])
    _submit(db, visitor_id="visitor-00000002", source="google", industries=["tech", "retail"])

    result = survey_routes.list_survey_responses(completed_only=False, page=1, page_size=20, db=db)

    assert result.total == 2
    assert result.completed_count == 1
    assert result.counts["source"] == {"google": 2}
    assert result.counts["industries"] == {"tech": 2, "retail": 1}
    emails = {item.user_email for item in result.items}
    assert emails == {user.email, None}

    only_done = survey_routes.list_survey_responses(completed_only=True, page=1, page_size=20, db=db)
    assert only_done.total == 1
