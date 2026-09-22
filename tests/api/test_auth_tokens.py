import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes.auth_tokens import (
    create_personal_access_token,
    list_personal_access_tokens,
    revoke_personal_access_token,
)
from app.db.base import Base
from app.models.user import User
from app.schemas.auth import PersonalAccessTokenCreate


@pytest.fixture
def db() -> Session:
    # Local db fixture (not tests/api/conftest.py's, which only has
    # SavedSearch) — these routes need PersonalAccessToken.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.PersonalAccessToken.__table__])
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


def _user() -> User:
    return User(id=uuid.uuid4(), email="person@example.com")


def test_create_personal_access_token_returns_the_raw_token_once(db):
    user = _user()

    created = create_personal_access_token(
        PersonalAccessTokenCreate(label="Browser extension"), current_user=user, db=db
    )

    assert created.label == "Browser extension"
    assert created.expires_at is None  # no expires_in_days given — never expires
    assert created.token  # the raw value, only ever returned here

    # What's actually stored is the hash, not the raw token — listing it
    # back never exposes that raw value again.
    stored = list_personal_access_tokens(current_user=user, db=db)
    assert len(stored) == 1
    assert stored[0].id == created.id
    assert not hasattr(stored[0], "token")


def test_create_personal_access_token_sets_an_expiry_when_given_one(db):
    user = _user()

    created = create_personal_access_token(
        PersonalAccessTokenCreate(label="Temp", expires_in_days=30), current_user=user, db=db
    )

    assert created.expires_at is not None


def test_list_personal_access_tokens_is_scoped_to_the_current_user(db):
    mine = _user()
    someone_else = _user()
    create_personal_access_token(PersonalAccessTokenCreate(label="Mine"), current_user=mine, db=db)
    create_personal_access_token(PersonalAccessTokenCreate(label="Theirs"), current_user=someone_else, db=db)

    results = list_personal_access_tokens(current_user=mine, db=db)

    assert [r.label for r in results] == ["Mine"]


def test_revoke_personal_access_token_marks_it_revoked(db):
    user = _user()
    created = create_personal_access_token(PersonalAccessTokenCreate(label="Browser extension"), current_user=user, db=db)

    revoke_personal_access_token(created.id, current_user=user, db=db)

    [token] = list_personal_access_tokens(current_user=user, db=db)
    assert token.revoked_at is not None


def test_revoke_personal_access_token_404s_for_someone_elses_token(db):
    owner = _user()
    someone_else = _user()
    created = create_personal_access_token(PersonalAccessTokenCreate(label="Owner's"), current_user=owner, db=db)

    with pytest.raises(HTTPException) as exc_info:
        revoke_personal_access_token(created.id, current_user=someone_else, db=db)
    assert exc_info.value.status_code == 404


def test_revoke_personal_access_token_404s_for_an_unknown_id(db):
    with pytest.raises(HTTPException) as exc_info:
        revoke_personal_access_token(uuid.uuid4(), current_user=_user(), db=db)
    assert exc_info.value.status_code == 404
