"""Tests for share attribution (app.api.routes.shares, app.services.job_shares)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes import shares as routes
from app.db.base import Base
from app.schemas.job_share import AttributionCreate, JobShareCreate

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
SID = "Abc123_-xyzXYZ0987"


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.User.__table__, m.JobPostingUrl.__table__, m.JobShare.__table__])
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(routes, "datetime", _Clock)


def _user(db, created=NOW - timedelta(minutes=5)) -> m.User:
    user = m.User(id=uuid.uuid4(), email=f"{uuid.uuid4()}@example.com", created_at=created)
    db.add(user)
    db.commit()
    return user


def _job(db) -> m.JobPostingUrl:
    n = uuid.uuid4().hex
    url = m.JobPostingUrl(url=f"https://example.com/{n}", normalized_url=f"https://example.com/{n}", url_hash=n,
                          domain="example.com")
    db.add(url)
    db.commit()
    return url


def _share(db, user, job, sid=SID, method="copy_link"):
    return routes.create_share(JobShareCreate(id=sid, job_url_id=job.id, method=method), current_user=user, db=db)


def test_a_share_is_recorded_once(db):
    user, job = _user(db), _job(db)
    assert _share(db, user, job).status_code == 204
    _share(db, user, job)  # sent again: fine
    [share] = db.query(m.JobShare).all()
    assert (share.id, share.user_id, share.job_url_id, share.method) == (SID, user.id, job.id, "copy_link")


@pytest.mark.parametrize("problem", ["malformed", "unknown job", "someone else's id"])
def test_a_bad_share_is_refused(db, problem):
    user, job = _user(db), _job(db)
    if problem == "malformed":
        args = dict(sid="not a valid id!!")
    elif problem == "unknown job":
        args = dict(job=m.JobPostingUrl(id=uuid.uuid4()))
    else:
        _share(db, _user(db), job)
        args = {}
    with pytest.raises(HTTPException) as exc:
        _share(db, user, args.pop("job", job), **args)
    assert exc.value.status_code == 422


def _attribute(db, user, **fields):
    return routes.create_attribution(AttributionCreate(**fields), current_user=user, db=db).recorded


def test_a_new_user_records_where_they_came_from_once(db):
    sharer, job = _user(db), _job(db)
    _share(db, sharer, job)
    user = _user(db)
    assert _attribute(db, user, share_id=SID, source="share", medium="share", landing_page=f"/jobs/{job.id}")
    assert (user.signup_share_id, user.signup_source, user.signup_landing_page) == (SID, "share", f"/jobs/{job.id}")
    assert user.attributed_at.replace(tzinfo=None) == NOW.replace(tzinfo=None)  # SQLite drops the zone
    assert not _attribute(db, user, source="google")  # first touch stands
    assert user.signup_source == "share"


def test_an_unregistered_share_id_is_kept(db):
    # Shared signed out, or from a static page: no row, but the id still counts.
    user = _user(db)
    assert _attribute(db, user, share_id=SID, source="share")
    assert user.signup_share_id == SID


def test_sharing_with_yourself_isnt_a_referral(db):
    user, job = _user(db), _job(db)
    _share(db, user, job)
    assert _attribute(db, user, share_id=SID, source="share")
    assert user.signup_share_id is None and user.signup_source == "share"


def test_old_accounts_and_bad_values_arent_recorded(db):
    old = _user(db, created=NOW - timedelta(days=3))
    assert not _attribute(db, old, source="google")
    assert old.attributed_at is None
    user = _user(db)
    assert _attribute(db, user, share_id="bad id", source="  ", campaign="x" * 300)
    assert user.signup_share_id is None and user.signup_source is None and len(user.signup_campaign) == 200
