import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes import admin as admin_routes
from app.api.routes.feedback import submit_feedback
from app.core.config import settings
from app.core.rate_limit import RateLimitExceeded
from app.db.base import Base
from app.models.enums import FeedbackKind, FeedbackStatus
from app.schemas.feedback import FeedbackCreate, FeedbackStatusUpdate
from app.services import feedback as feedback_service


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.User.__table__, m.Feedback.__table__])
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def no_email(monkeypatch):
    send = Mock()
    monkeypatch.setattr(feedback_service, "send_feedback_notification_email", send)
    return send


def _make_user(db) -> m.User:
    user = m.User(id=uuid.uuid4(), email=f"{uuid.uuid4()}@example.com")
    db.add(user)
    db.commit()
    return user


def _request(user_agent: str = "pytest-agent") -> Mock:
    request = Mock()
    request.headers = {"user-agent": user_agent}
    return request


def test_submit_feedback_stores_it_as_new(db):
    user = _make_user(db)
    payload = FeedbackCreate(kind=FeedbackKind.BUG, message="  The chart is blank  ", rating=2, page_url="/board")

    feedback = submit_feedback(payload, _request(), current_user=user, db=db)

    assert feedback.user_id == user.id
    assert feedback.kind == FeedbackKind.BUG
    assert feedback.message == "The chart is blank"
    assert feedback.rating == 2
    assert feedback.page_url == "/board"
    assert feedback.user_agent == "pytest-agent"
    assert feedback.status == FeedbackStatus.NEW


def test_submit_feedback_emails_admins(db, no_email, monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", "b@example.com, a@example.com")
    user = _make_user(db)

    submit_feedback(FeedbackCreate(message="Hi"), _request(), current_user=user, db=db)

    no_email.assert_called_once()
    assert no_email.call_args.args[0] == ["a@example.com", "b@example.com"]
    assert no_email.call_args.kwargs["from_user_email"] == user.email


def test_submit_feedback_survives_a_failed_notification(db, no_email, monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", "a@example.com")
    no_email.side_effect = RuntimeError("SES down")
    user = _make_user(db)

    feedback = submit_feedback(FeedbackCreate(message="Hi"), _request(), current_user=user, db=db)

    assert db.get(m.Feedback, feedback.id) is not None


def test_submit_feedback_is_rate_limited_per_user(db, monkeypatch):
    monkeypatch.setattr(settings, "feedback_rate_limit_max_per_user", 2)
    user = _make_user(db)
    other = _make_user(db)
    for _ in range(2):
        submit_feedback(FeedbackCreate(message="Hi"), _request(), current_user=user, db=db)

    with pytest.raises(HTTPException) as exc_info:
        submit_feedback(FeedbackCreate(message="Hi"), _request(), current_user=user, db=db)
    assert exc_info.value.status_code == 429

    # Another user's quota is separate.
    submit_feedback(FeedbackCreate(message="Hi"), _request(), current_user=other, db=db)


def test_rate_limit_window_expires(db, monkeypatch):
    monkeypatch.setattr(settings, "feedback_rate_limit_max_per_user", 1)
    user = _make_user(db)
    feedback_service.create_feedback(db, user, FeedbackCreate(message="Hi"))
    db.commit()

    with pytest.raises(RateLimitExceeded):
        feedback_service.create_feedback(db, user, FeedbackCreate(message="Hi"))

    later = datetime.now(timezone.utc) + timedelta(minutes=settings.feedback_rate_limit_window_minutes + 1)
    feedback_service.create_feedback(db, user, FeedbackCreate(message="Hi"), now=later)


def test_feedback_create_rejects_bad_input():
    with pytest.raises(ValueError):
        FeedbackCreate(message="")
    with pytest.raises(ValueError):
        FeedbackCreate(message="Hi", rating=6)


def test_admin_list_feedback_filters_and_counts_new(db):
    user = _make_user(db)
    bug = submit_feedback(FeedbackCreate(kind=FeedbackKind.BUG, message="bug"), _request(), current_user=user, db=db)
    submit_feedback(FeedbackCreate(kind=FeedbackKind.IDEA, message="idea"), _request(), current_user=user, db=db)
    db.commit()
    admin_routes.update_feedback_status(bug.id, FeedbackStatusUpdate(status=FeedbackStatus.RESOLVED), db=db)
    db.commit()

    everything = admin_routes.list_feedback(status_filter=None, kind=None, page=1, page_size=20, db=db)
    assert everything.total == 2
    assert everything.new_count == 1
    assert {item.user_email for item in everything.items} == {user.email}

    resolved = admin_routes.list_feedback(
        status_filter=FeedbackStatus.RESOLVED, kind=None, page=1, page_size=20, db=db
    )
    assert [item.message for item in resolved.items] == ["bug"]

    ideas = admin_routes.list_feedback(status_filter=None, kind=FeedbackKind.IDEA, page=1, page_size=20, db=db)
    assert [item.message for item in ideas.items] == ["idea"]


def test_admin_update_feedback_status_404s_for_unknown_id(db):
    with pytest.raises(HTTPException) as exc_info:
        admin_routes.update_feedback_status(uuid.uuid4(), FeedbackStatusUpdate(status=FeedbackStatus.READ), db=db)
    assert exc_info.value.status_code == 404
