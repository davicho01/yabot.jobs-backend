from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.core.config import settings
from app.db.base import Base
from app.services import auth as auth_service


# SQLite hands DateTime(timezone=True) back naive, Postgres aware — same
# shim as tests/test_oauth_service.py's.
def _reattach_utc(session: Session, instance: object) -> None:
    for field in ("expires_at", "consumed_at", "created_at"):
        value = getattr(instance, field, None)
        if isinstance(value, datetime) and value.tzinfo is None:
            setattr(instance, field, value.replace(tzinfo=timezone.utc))


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.User.__table__, m.MagicLinkToken.__table__])
    # Not expired on commit, so a commit doesn't reload them naive.
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    event.listen(session, "loaded_as_persistent", _reattach_utc)
    yield session
    session.close()


def _code_for(db, email="person@example.com") -> str:
    user = auth_service.get_or_create_user(db, email)
    code = auth_service.create_login_code(db, user)
    db.commit()
    return code


def test_the_emailed_code_signs_in_once(db):
    code = _code_for(db)
    assert len(code) == 6 and code.isdigit()

    user = auth_service.verify_login_code(db, "Person@Example.com ", f"{code[:3]} {code[3:]}")
    assert user.email == "person@example.com"
    db.commit()  # what the request does (get_db)

    with pytest.raises(auth_service.AuthError, match="expired"):
        auth_service.verify_login_code(db, "person@example.com", code)


def test_a_wrong_code_counts_and_too_many_lock_the_code(db):
    code = _code_for(db)
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(settings.login_code_max_attempts):
        with pytest.raises(auth_service.AuthError, match="isn't right"):
            auth_service.verify_login_code(db, "person@example.com", wrong)

    with pytest.raises(auth_service.AuthError, match="Too many"):
        auth_service.verify_login_code(db, "person@example.com", code)


def test_only_the_newest_code_works(db):
    first = _code_for(db)
    second = _code_for(db)
    if first == second:
        pytest.skip("drew the same code twice")

    with pytest.raises(auth_service.AuthError, match="isn't right"):
        auth_service.verify_login_code(db, "person@example.com", first)
    assert auth_service.verify_login_code(db, "person@example.com", second).email == "person@example.com"


def test_an_expired_code_is_refused(db):
    code = _code_for(db)
    token = db.scalar(select(m.MagicLinkToken))
    token.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    with pytest.raises(auth_service.AuthError, match="expired"):
        auth_service.verify_login_code(db, "person@example.com", code)


def test_an_unknown_email_gets_the_same_answer_as_a_wrong_code(db):
    with pytest.raises(auth_service.AuthError, match="isn't right"):
        auth_service.verify_login_code(db, "nobody@example.com", "123456")
