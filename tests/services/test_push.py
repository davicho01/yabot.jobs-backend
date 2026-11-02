"""Tests for app.services.push — device registration and the Expo send.
In-memory SQLite with just User + DeviceToken; httpx.post is patched."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.models as m
from app.db.base import Base
from app.services import push


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.User.__table__, m.DeviceToken.__table__])
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    yield session
    session.close()


def _user(db) -> m.User:
    user = m.User(email=f"{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    return user


def _expo_response(*tickets: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"data": list(tickets)}
    return response


def test_register_moves_token_to_new_user(db):
    first, second = _user(db), _user(db)
    push.register_device(db, first.id, "ExponentPushToken[abc]", "ios")
    push.register_device(db, second.id, "ExponentPushToken[abc]", "ios")

    rows = db.scalars(select(m.DeviceToken)).all()
    assert [r.user_id for r in rows] == [second.id]


def test_unregister_only_removes_own_token(db):
    owner, other = _user(db), _user(db)
    push.register_device(db, owner.id, "ExponentPushToken[abc]", "android")
    push.unregister_device(db, other.id, "ExponentPushToken[abc]")
    assert len(db.scalars(select(m.DeviceToken)).all()) == 1
    push.unregister_device(db, owner.id, "ExponentPushToken[abc]")
    assert db.scalars(select(m.DeviceToken)).all() == []


def test_send_push_counts_accepted_and_prunes_dead_tokens(db):
    user = _user(db)
    push.register_device(db, user.id, "ExponentPushToken[live]", "ios")
    push.register_device(db, user.id, "ExponentPushToken[dead]", "android")
    # Same query (so same order) send_push uses to pair tickets with tokens.
    tokens = list(db.scalars(select(m.DeviceToken.token).where(m.DeviceToken.user_id == user.id)))
    tickets = [
        {"status": "ok"} if t.endswith("[live]") else {"status": "error", "details": {"error": "DeviceNotRegistered"}}
        for t in tokens
    ]
    with patch("app.services.push.httpx.post", return_value=_expo_response(*tickets)):
        accepted = push.send_push(db, user.id, "title", "body")

    assert accepted == 1
    assert list(db.scalars(select(m.DeviceToken.token))) == ["ExponentPushToken[live]"]


def test_send_push_swallows_expo_failure(db):
    user = _user(db)
    push.register_device(db, user.id, "ExponentPushToken[live]", "ios")
    with patch("app.services.push.httpx.post", side_effect=RuntimeError("boom")):
        assert push.send_push(db, user.id, "title", "body") == 0
    assert len(db.scalars(select(m.DeviceToken)).all()) == 1


def test_send_push_without_devices_makes_no_request(db):
    user = _user(db)
    with patch("app.services.push.httpx.post") as post:
        assert push.send_push(db, user.id, "title", "body") == 0
    post.assert_not_called()
