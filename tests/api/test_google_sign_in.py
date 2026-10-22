from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes.auth import sign_in_with_google
from app.core.config import settings
from app.db.base import Base
from app.models.enums import UserRole, UserStatus
from app.schemas.auth import GoogleSignInRequest
from app.services import auth as auth_service

CLIENT_ID = "test-client.apps.googleusercontent.com"


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.User.__table__, m.UserSession.__table__])
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def google_enabled(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "admin_emails", "")


@pytest.fixture
def google_claims(monkeypatch):
    """What Google's verifier returns; tests edit it before signing in."""
    claims = {"email": "Ada@Gmail.com", "email_verified": True, "name": "Ada Lovelace"}

    def fake_verify(credential, _request, audience):
        assert audience == CLIENT_ID
        if credential == "bad":
            raise ValueError("Token used too late")
        return claims

    monkeypatch.setattr(auth_service.google_id_token, "verify_oauth2_token", fake_verify)
    return claims


def _sign_in(db, credential="good"):
    response = Response()
    request = SimpleNamespace(headers={"user-agent": "pytest"}, client=SimpleNamespace(host="127.0.0.1"))
    result = sign_in_with_google(GoogleSignInRequest(credential=credential), request, response, db)
    return result, response


def test_new_user_is_created_active_with_a_session(db, google_claims):
    result, response = _sign_in(db)

    assert result.user.email == "ada@gmail.com"
    assert result.user.display_name == "Ada Lovelace"
    db.flush()  # get_db commits in the app; this session doesn't autoflush
    user = db.scalar(select(m.User))
    assert user.status == UserStatus.ACTIVE
    assert user.last_login_at is not None
    assert db.scalar(select(m.UserSession)).user_id == user.id
    assert settings.session_cookie_name in response.headers["set-cookie"]


def test_existing_email_user_gets_the_same_account(db, google_claims):
    existing = auth_service.get_or_create_user(db, "ada@gmail.com")
    existing.display_name = "Ada"
    db.commit()

    result, _ = _sign_in(db)

    assert result.user.id == existing.id
    assert result.user.display_name == "Ada"  # an existing name isn't overwritten
    assert db.scalar(select(m.User).where(m.User.id != existing.id)) is None


def test_unverified_google_email_is_rejected(db, google_claims):
    google_claims["email_verified"] = False

    with pytest.raises(HTTPException) as exc:
        _sign_in(db)

    assert exc.value.status_code == 400
    assert db.scalar(select(m.User)) is None


def test_invalid_token_is_rejected(db, google_claims):
    with pytest.raises(HTTPException) as exc:
        _sign_in(db, credential="bad")

    assert exc.value.status_code == 400


def test_disabled_when_no_client_id(db, google_claims, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "")

    with pytest.raises(HTTPException) as exc:
        _sign_in(db)

    assert exc.value.status_code == 404


def test_admin_email_is_promoted(db, google_claims, monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", "ada@gmail.com")

    result, _ = _sign_in(db)

    assert result.user.role == UserRole.ADMIN
