"""Tests for the follow-up reminder sweep — app.services.follow_up_reminders.
Same in-memory SQLite style as test_saved_search_alerts.py, with its own
table set (User + UserJobApplication on top of JobPosting/JobPostingUrl).
"""

import itertools
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models as m
from app.db.base import Base
from app.models.enums import ScanStatus
from app.services.follow_up_reminders import send_due_reminders

_url_counter = itertools.count()


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[m.User.__table__, m.JobPostingUrl.__table__, m.JobPosting.__table__, m.UserJobApplication.__table__],
    )
    # expire_on_commit=False — see test_saved_search_alerts.py's identical
    # note: SQLite's text-based DateTime round-trips a tz-aware Python
    # datetime as naive, which breaks comparisons and, worse, the
    # follow_up_at <= today query itself once one side has round-tripped and
    # the other hasn't.
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    yield session
    session.close()


def _make_user(db) -> m.User:
    user = m.User(email=f"{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    return user


def _make_application(db, user, *, follow_up_at=None, follow_up_reminded_at=None, is_archived=False, status="saved", title="Engineer", company="Acme") -> m.UserJobApplication:
    n = next(_url_counter)
    url = m.JobPostingUrl(
        url=f"https://example.com/jobs/followup-{n}",
        normalized_url=f"https://example.com/jobs/followup-{n}",
        url_hash=f"followup-hash-{n}",
        domain="example.com",
    )
    db.add(url)
    db.flush()
    posting = m.JobPosting(url_id=url.id, title=title, company_name=company, extraction_status=ScanStatus.SUCCESS)
    db.add(posting)
    db.flush()
    application = m.UserJobApplication(
        user_id=user.id,
        job_posting_id=posting.id,
        status=status,
        is_archived=is_archived,
        follow_up_at=follow_up_at,
        follow_up_reminded_at=follow_up_reminded_at,
    )
    db.add(application)
    db.commit()
    return application


@patch("app.services.follow_up_reminders.send_follow_up_reminder_email")
def test_sends_a_reminder_for_a_due_application(send, db):
    today = date.today()
    user = _make_user(db)
    application = _make_application(db, user, follow_up_at=today)

    sent = send_due_reminders(db, today=today)

    assert sent == 1
    send.assert_called_once()
    assert send.call_args.args[0] == user.email
    assert application.follow_up_reminded_at is not None


@patch("app.services.follow_up_reminders.send_follow_up_reminder_email")
def test_sends_for_a_date_in_the_past_too(send, db):
    today = date.today()
    user = _make_user(db)
    _make_application(db, user, follow_up_at=today - timedelta(days=5))

    sent = send_due_reminders(db, today=today)

    assert sent == 1


@patch("app.services.follow_up_reminders.send_follow_up_reminder_email")
def test_skips_a_future_follow_up_date(send, db):
    today = date.today()
    user = _make_user(db)
    _make_application(db, user, follow_up_at=today + timedelta(days=1))

    sent = send_due_reminders(db, today=today)

    assert sent == 0
    send.assert_not_called()


@patch("app.services.follow_up_reminders.send_follow_up_reminder_email")
def test_skips_one_already_reminded_about(send, db):
    today = date.today()
    user = _make_user(db)
    _make_application(db, user, follow_up_at=today, follow_up_reminded_at=datetime.now(timezone.utc))

    sent = send_due_reminders(db, today=today)

    assert sent == 0


@patch("app.services.follow_up_reminders.send_follow_up_reminder_email")
def test_skips_an_archived_application(send, db):
    today = date.today()
    user = _make_user(db)
    _make_application(db, user, follow_up_at=today, is_archived=True)

    sent = send_due_reminders(db, today=today)

    assert sent == 0


@pytest.mark.parametrize("status", ["rejected", "withdrawn"])
@patch("app.services.follow_up_reminders.send_follow_up_reminder_email")
def test_skips_a_terminal_status(send, db, status):
    today = date.today()
    user = _make_user(db)
    _make_application(db, user, follow_up_at=today, status=status)

    sent = send_due_reminders(db, today=today)

    assert sent == 0


@patch("app.services.follow_up_reminders.send_follow_up_reminder_email")
def test_does_not_skip_an_active_non_terminal_status(send, db):
    today = date.today()
    user = _make_user(db)
    _make_application(db, user, follow_up_at=today, status="interviewing")

    sent = send_due_reminders(db, today=today)

    assert sent == 1


@patch("app.services.follow_up_reminders.send_follow_up_reminder_email")
def test_one_failure_does_not_stop_the_rest(send, db):
    today = date.today()
    user_a = _make_user(db)
    user_b = _make_user(db)
    _make_application(db, user_a, follow_up_at=today, title="First")
    _make_application(db, user_b, follow_up_at=today, title="Second")
    send.side_effect = [RuntimeError("SES is down"), None]

    sent = send_due_reminders(db, today=today)

    assert sent == 1
    assert send.call_count == 2
