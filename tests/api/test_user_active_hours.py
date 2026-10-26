import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api import deps
from app.db.base import Base
from app.models.user import User
from app.models.user_activity import UserActiveHour
from app.services import auth as auth_service


@pytest.fixture
def db() -> Session:
    # Local db fixture (not tests/api/conftest.py's, which only has
    # SavedSearch) — recording needs UserActiveHour.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.UserActiveHour.__table__])
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _fresh_recorded_cache(monkeypatch):
    monkeypatch.setattr(auth_service, "_recorded_active", (datetime.min, set()))


def _user() -> User:
    return User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@example.com")


def _rows(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(UserActiveHour)) or 0


def test_records_one_row_per_user_per_hour(db):
    user = _user()

    auth_service.record_active_hour(db, user)
    auth_service.record_active_hour(db, user)

    assert _rows(db) == 1


def test_a_second_instance_recording_the_same_hour_is_absorbed(db, monkeypatch):
    user = _user()
    auth_service.record_active_hour(db, user)

    # Another instance has its own (empty) cache and inserts again.
    monkeypatch.setattr(auth_service, "_recorded_active", (datetime.min, set()))
    auth_service.record_active_hour(db, user)

    assert _rows(db) == 1


def test_each_user_gets_their_own_row(db):
    auth_service.record_active_hour(db, _user())
    auth_service.record_active_hour(db, _user())

    assert _rows(db) == 2


def test_resolved_requests_are_recorded_anonymous_ones_are_not(db, monkeypatch):
    user = _user()

    monkeypatch.setattr(deps, "_resolve_user", lambda request, db: None)
    assert deps.get_current_user_optional(request=None, db=db) is None
    assert _rows(db) == 0

    monkeypatch.setattr(deps, "_resolve_user", lambda request, db: user)
    assert deps.get_current_user_optional(request=None, db=db) is user
    assert _rows(db) == 1
