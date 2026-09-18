"""Service-layer tests for the MCP server's OAuth 2.1 flow
(app/services/oauth.py) — dynamic client registration through
authorize -> approve -> exchange -> refresh -> revoke, plus the
rejection cases (expired/consumed code, wrong client_id, revoked
refresh token).

Runs against an in-memory SQLite engine rather than the app's
configured Postgres — no DB/TestClient fixtures exist elsewhere in this
repo (see tests/conftest.py), so this stays self-contained: only the
tables these tests actually touch are created (other models use
Postgres-only JSONB and won't compile under SQLite).
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.db.base import Base
from app.models.enums import UserRole, UserStatus
from app.services import oauth as oauth_service

# Postgres (what production actually runs on) round-trips `DateTime(timezone=True)`
# columns as tz-aware; SQLite has no native tz-aware datetime type and hands back
# naive ones on reload (e.g. once the session's weak identity map drops an object
# and a later query reconstructs it fresh) — which is a SQLite-testing artifact,
# not a bug in the service code under test. Reattach UTC on reload so these tests
# reflect real (Postgres) behavior.
_NAIVE_DATETIME_FIELDS = ("expires_at", "consumed_at", "revoked_at", "last_used_at", "created_at")


def _reattach_utc(session: Session, instance: object) -> None:
    for field in _NAIVE_DATETIME_FIELDS:
        value = getattr(instance, field, None)
        if isinstance(value, datetime) and value.tzinfo is None:
            setattr(instance, field, value.replace(tzinfo=timezone.utc))


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            m.User.__table__,
            m.PersonalAccessToken.__table__,
            m.OAuthClient.__table__,
            m.OAuthAuthorizationRequest.__table__,
            m.OAuthRefreshToken.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    event.listen(session, "loaded_as_persistent", _reattach_utc)
    yield session
    session.close()


@pytest.fixture
def user(db: Session) -> m.User:
    user = m.User(email="dev@example.com", status=UserStatus.ACTIVE, role=UserRole.USER)
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def client(db: Session) -> m.OAuthClient:
    return oauth_service.register_client(db, "client-1", "Claude Desktop", ["https://claude.ai/oauth/callback"])


def _authorize(db: Session, client: m.OAuthClient, **overrides) -> m.OAuthAuthorizationRequest:
    defaults = dict(
        redirect_uri="https://claude.ai/oauth/callback",
        code_challenge="challenge",
        code_challenge_method="S256",
        scopes=None,
        resource=None,
        state="xyz",
    )
    defaults.update(overrides)
    return oauth_service.create_authorization_request(db, client, **defaults)


def test_register_and_get_client(db: Session):
    client = oauth_service.register_client(db, "client-1", "Claude Desktop", ["https://claude.ai/oauth/callback"])
    fetched = oauth_service.get_client(db, client.client_id)
    assert fetched is not None
    assert fetched.client_name == "Claude Desktop"
    assert fetched.redirect_uris == ["https://claude.ai/oauth/callback"]
    assert oauth_service.get_client(db, "no-such-client") is None


def test_full_authorization_code_round_trip(db: Session, user: m.User, client: m.OAuthClient):
    request = _authorize(db, client)

    # Pending: not yet decided, findable by request_id.
    pending = oauth_service.get_authorization_request(db, request.id)
    assert pending is not None
    assert pending.user_id is None

    request, raw_code = oauth_service.approve_authorization_request(db, request.id, user)
    assert request.user_id == user.id
    assert request.code_hash is not None

    # A second approval attempt on the same (already-decided) request is rejected.
    with pytest.raises(oauth_service.OAuthError):
        oauth_service.approve_authorization_request(db, request.id, user)

    loaded = oauth_service.load_authorization_code(db, raw_code)
    assert loaded is not None
    assert loaded.id == request.id

    access_token, refresh_token, expires_in = oauth_service.exchange_authorization_code(db, loaded, client)
    assert access_token and refresh_token
    assert expires_in == int(oauth_service.ACCESS_TOKEN_TTL.total_seconds())

    # The code is single-use: a second exchange attempt must fail.
    with pytest.raises(oauth_service.OAuthError):
        oauth_service.exchange_authorization_code(db, loaded, client)

    # And it can no longer be loaded either, since it's consumed.
    assert oauth_service.load_authorization_code(db, raw_code) is None

    # The minted access token really is a live PersonalAccessToken for this user.
    from app.services.auth import get_user_by_pat

    assert get_user_by_pat(db, access_token).id == user.id


def test_deny_authorization_request(db: Session, user: m.User, client: m.OAuthClient):
    request = _authorize(db, client)
    denied = oauth_service.deny_authorization_request(db, request.id)
    assert denied.consumed_at is not None
    # A denied request can't later be approved.
    assert oauth_service.get_authorization_request(db, request.id) is None


def test_expired_authorization_request_is_not_loadable(db: Session, client: m.OAuthClient):
    request = _authorize(db, client)
    request.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert oauth_service.get_authorization_request(db, request.id) is None


def test_refresh_token_rotation(db: Session, user: m.User, client: m.OAuthClient):
    request = _authorize(db, client)
    request, raw_code = oauth_service.approve_authorization_request(db, request.id, user)
    loaded = oauth_service.load_authorization_code(db, raw_code)
    access_token, refresh_token, _ = oauth_service.exchange_authorization_code(db, loaded, client)

    result = oauth_service.exchange_refresh_token(db, refresh_token, client.client_id)
    assert result is not None
    new_access_token, new_refresh_token, expires_in = result
    assert new_access_token != access_token
    assert new_refresh_token != refresh_token

    # The old refresh token is single-use (rotated away) ...
    assert oauth_service.exchange_refresh_token(db, refresh_token, client.client_id) is None
    # ... and so is the old access token, since rotation revokes it.
    from app.services.auth import get_user_by_pat

    assert get_user_by_pat(db, access_token) is None
    assert get_user_by_pat(db, new_access_token).id == user.id


def test_refresh_token_rejects_wrong_client(db: Session, user: m.User, client: m.OAuthClient):
    other_client = oauth_service.register_client(db, "client-2", "Someone Else", ["https://evil.example/callback"])
    request = _authorize(db, client)
    request, raw_code = oauth_service.approve_authorization_request(db, request.id, user)
    loaded = oauth_service.load_authorization_code(db, raw_code)
    _, refresh_token, _ = oauth_service.exchange_authorization_code(db, loaded, client)

    assert oauth_service.exchange_refresh_token(db, refresh_token, other_client.client_id) is None


def test_revoke_access_token_also_revokes_paired_refresh_token(db: Session, user: m.User, client: m.OAuthClient):
    request = _authorize(db, client)
    request, raw_code = oauth_service.approve_authorization_request(db, request.id, user)
    loaded = oauth_service.load_authorization_code(db, raw_code)
    access_token, refresh_token, _ = oauth_service.exchange_authorization_code(db, loaded, client)

    oauth_service.revoke_oauth_token(db, access_token, "access_token")

    from app.services.auth import get_user_by_pat

    assert get_user_by_pat(db, access_token) is None
    assert oauth_service.exchange_refresh_token(db, refresh_token, client.client_id) is None


def test_revoke_refresh_token_also_revokes_paired_access_token(db: Session, user: m.User, client: m.OAuthClient):
    request = _authorize(db, client)
    request, raw_code = oauth_service.approve_authorization_request(db, request.id, user)
    loaded = oauth_service.load_authorization_code(db, raw_code)
    access_token, refresh_token, _ = oauth_service.exchange_authorization_code(db, loaded, client)

    oauth_service.revoke_oauth_token(db, refresh_token, "refresh_token")

    from app.services.auth import get_user_by_pat

    assert get_user_by_pat(db, access_token) is None
    assert oauth_service.exchange_refresh_token(db, refresh_token, client.client_id) is None


def test_revoke_unknown_token_is_a_noop(db: Session):
    oauth_service.revoke_oauth_token(db, "not-a-real-token", None)
