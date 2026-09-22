"""Tests for the saved-search alert sweep — app.services.saved_search_alerts.
Runs against an in-memory SQLite engine, same style as tests/services/conftest.py's
scan_db, but with its own table set (User + SavedSearch on top) since those
other tests don't need either.
"""

import itertools
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models as m
from app.db.base import Base
from app.models.enums import ScanStatus
from app.services.saved_search_alerts import MAX_DIGEST_ITEMS, sweep_saved_searches

_url_counter = itertools.count()


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[m.User.__table__, m.JobPostingUrl.__table__, m.JobPosting.__table__, m.SavedSearch.__table__],
    )
    # expire_on_commit=False: SQLite has no real timezone-aware storage (it's
    # text underneath), so the default's post-commit reload comes back with
    # tzinfo stripped — breaking both direct tz-aware-vs-naive comparisons in
    # assertions and, worse, the `scanned_at >= since` query itself once one
    # side round-trips through SQLite's text format and the other doesn't.
    # Real Postgres (production) doesn't have this problem.
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    yield session
    session.close()


def _make_user(db) -> m.User:
    user = m.User(email=f"{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    return user


def _make_scanned_posting(db, *, scanned_at: datetime, title="Engineer", company="Acme") -> m.JobPosting:
    n = next(_url_counter)
    url = m.JobPostingUrl(
        url=f"https://example.com/jobs/alert-{n}",
        normalized_url=f"https://example.com/jobs/alert-{n}",
        url_hash=f"alert-hash-{n}",
        domain="example.com",
    )
    db.add(url)
    db.flush()
    posting = m.JobPosting(
        url_id=url.id, title=title, company_name=company, extraction_status=ScanStatus.SUCCESS, scanned_at=scanned_at
    )
    db.add(posting)
    db.commit()
    return posting


def _make_saved_search(db, user, **kwargs) -> m.SavedSearch:
    saved = m.SavedSearch(user_id=user.id, **kwargs)
    db.add(saved)
    db.commit()
    return saved


@patch("app.services.saved_search_alerts.send_saved_search_digest_email")
def test_sweep_sends_a_digest_for_a_newly_scanned_match(send, db):
    now = datetime.now(timezone.utc)
    user = _make_user(db)
    saved_search = _make_saved_search(db, user, q="engineer", created_at=now - timedelta(days=1))
    _make_scanned_posting(db, scanned_at=now, title="Backend Engineer")

    sent = sweep_saved_searches(db, now=now)

    assert sent == 1
    send.assert_called_once()
    assert send.call_args.args[0] == user.email
    assert len(send.call_args.kwargs["matches"]) == 1
    assert send.call_args.kwargs["more_count"] == 0
    assert saved_search.last_alerted_at == now


@patch("app.services.saved_search_alerts.send_saved_search_digest_email")
def test_sweep_ignores_postings_scanned_before_the_search_was_created(send, db):
    now = datetime.now(timezone.utc)
    user = _make_user(db)
    _make_saved_search(db, user, q="engineer", created_at=now)
    # Scanned well before the saved search existed — not "new" to it.
    _make_scanned_posting(db, scanned_at=now - timedelta(days=5), title="Backend Engineer")

    sent = sweep_saved_searches(db, now=now)

    assert sent == 0
    send.assert_not_called()


@patch("app.services.saved_search_alerts.send_saved_search_digest_email")
def test_sweep_only_looks_at_postings_scanned_since_the_last_alert(send, db):
    now = datetime.now(timezone.utc)
    user = _make_user(db)
    saved_search = _make_saved_search(
        db, user, created_at=now - timedelta(days=10), last_alerted_at=now - timedelta(hours=1)
    )
    # Scanned after the search was created but before the last alert — already covered.
    _make_scanned_posting(db, scanned_at=now - timedelta(hours=2), title="Old match")
    # Scanned since the last alert — genuinely new.
    _make_scanned_posting(db, scanned_at=now, title="New match")

    sweep_saved_searches(db, now=now)

    assert [job.posting.title for job in send.call_args.kwargs["matches"]] == ["New match"]


@patch("app.services.saved_search_alerts.send_saved_search_digest_email")
def test_sweep_leaves_last_alerted_at_untouched_when_nothing_new(send, db):
    now = datetime.now(timezone.utc)
    user = _make_user(db)
    previous_alert = now - timedelta(hours=1)
    saved_search = _make_saved_search(db, user, q="engineer", last_alerted_at=previous_alert)

    sent = sweep_saved_searches(db, now=now)

    assert sent == 0
    send.assert_not_called()
    # Not bumped to `now` — nothing new means the window a future sweep
    # checks against shouldn't move past what's actually been covered.
    assert saved_search.last_alerted_at == previous_alert


@patch("app.services.saved_search_alerts.send_saved_search_digest_email")
def test_sweep_caps_matches_and_reports_the_remainder(send, db):
    now = datetime.now(timezone.utc)
    user = _make_user(db)
    saved_search = _make_saved_search(db, user, created_at=now - timedelta(days=1))
    for i in range(MAX_DIGEST_ITEMS + 3):
        _make_scanned_posting(db, scanned_at=now, title=f"Match {i}")

    sweep_saved_searches(db, now=now)

    assert len(send.call_args.kwargs["matches"]) == MAX_DIGEST_ITEMS
    assert send.call_args.kwargs["more_count"] == 3
    assert saved_search.last_alerted_at == now


@patch("app.services.saved_search_alerts.send_saved_search_digest_email")
def test_sweep_is_scoped_per_search_not_shared_across_users(send, db):
    now = datetime.now(timezone.utc)
    user_a = _make_user(db)
    user_b = _make_user(db)
    _make_saved_search(db, user_a, q="frontend", created_at=now - timedelta(days=1))
    _make_saved_search(db, user_b, q="backend", created_at=now - timedelta(days=1))
    _make_scanned_posting(db, scanned_at=now, title="Frontend Engineer")
    _make_scanned_posting(db, scanned_at=now, title="Backend Engineer")

    sent = sweep_saved_searches(db, now=now)

    assert sent == 2
    sent_titles = {call.args[0]: [j.posting.title for j in call.kwargs["matches"]] for call in send.call_args_list}
    assert sent_titles[user_a.email] == ["Frontend Engineer"]
    assert sent_titles[user_b.email] == ["Backend Engineer"]


@patch("app.services.saved_search_alerts.send_saved_search_digest_email")
def test_sweep_skips_a_failing_search_without_losing_the_others(send, db):
    now = datetime.now(timezone.utc)
    user_a = _make_user(db)
    user_b = _make_user(db)
    _make_saved_search(db, user_a, q="frontend", created_at=now - timedelta(days=1))
    _make_saved_search(db, user_b, q="backend", created_at=now - timedelta(days=1))
    _make_scanned_posting(db, scanned_at=now, title="Frontend Engineer")
    _make_scanned_posting(db, scanned_at=now, title="Backend Engineer")

    # First call (whichever search hits it first) blows up; the sweep must
    # still get to the second search instead of the whole run failing.
    send.side_effect = [RuntimeError("SES is down"), None]

    sent = sweep_saved_searches(db, now=now)

    assert sent == 1
    assert send.call_count == 2
