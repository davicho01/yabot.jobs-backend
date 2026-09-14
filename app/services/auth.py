import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import generate_token, hash_token
from app.models.auth import MagicLinkToken, PersonalAccessToken, UserSession
from app.models.enums import UserStatus
from app.models.user import User


class AuthError(Exception):
    pass


def get_or_create_user(db: Session, email: str) -> User:
    email = email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, status=UserStatus.INVITED)
        db.add(user)
        db.flush()
    return user


def create_magic_link(db: Session, user: User) -> str:
    """Create a magic link token for the user and return the raw token
    (only ever available here — the DB stores just its hash).
    """
    raw_token = generate_token()
    db.add(
        MagicLinkToken(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.magic_link_ttl_minutes),
        )
    )
    return raw_token


def redeem_magic_link(db: Session, raw_token: str) -> User:
    token_hash = hash_token(raw_token)
    link = db.scalar(select(MagicLinkToken).where(MagicLinkToken.token_hash == token_hash))

    if link is None:
        raise AuthError("Invalid or expired link.")
    if link.consumed_at is not None:
        raise AuthError("This link has already been used.")
    if link.expires_at < datetime.now(timezone.utc):
        raise AuthError("This link has expired.")

    link.consumed_at = datetime.now(timezone.utc)

    user = db.get(User, link.user_id)
    if user is None:
        raise AuthError("Invalid or expired link.")
    if user.status == UserStatus.INVITED:
        user.status = UserStatus.ACTIVE
    user.last_login_at = datetime.now(timezone.utc)
    return user


def create_session(db: Session, user: User, user_agent: str | None, ip_address: str | None) -> str:
    raw_token = generate_token()
    db.add(
        UserSession(
            user_id=user.id,
            session_token_hash=hash_token(raw_token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=settings.session_ttl_days),
            user_agent=user_agent,
            ip_address=ip_address,
        )
    )
    return raw_token


def get_user_by_session_token(db: Session, raw_token: str) -> User | None:
    token_hash = hash_token(raw_token)
    session = db.scalar(select(UserSession).where(UserSession.session_token_hash == token_hash))

    if session is None or session.revoked_at is not None:
        return None
    if session.expires_at < datetime.now(timezone.utc):
        return None

    session.last_used_at = datetime.now(timezone.utc)
    return db.get(User, session.user_id)


def revoke_session(db: Session, raw_token: str) -> None:
    token_hash = hash_token(raw_token)
    session = db.scalar(select(UserSession).where(UserSession.session_token_hash == token_hash))
    if session is not None:
        session.revoked_at = datetime.now(timezone.utc)


# --- Personal access tokens (for programmatic clients, e.g. mcp_server/) ---


def create_personal_access_token(
    db: Session, user: User, label: str, expires_in_days: int | None
) -> tuple[PersonalAccessToken, str]:
    """Mint a new personal access token for `user`. Returns the row plus
    the raw token — the raw value is only ever available here, at
    creation time; only its hash is persisted.
    """
    raw_token = generate_token()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=expires_in_days) if expires_in_days is not None else None
    )
    token = PersonalAccessToken(
        user_id=user.id, label=label, token_hash=hash_token(raw_token), expires_at=expires_at
    )
    db.add(token)
    db.flush()
    return token, raw_token


def get_user_by_pat(db: Session, raw_token: str) -> User | None:
    token_hash = hash_token(raw_token)
    token = db.scalar(select(PersonalAccessToken).where(PersonalAccessToken.token_hash == token_hash))

    if token is None or token.revoked_at is not None:
        return None
    if token.expires_at is not None and token.expires_at < datetime.now(timezone.utc):
        return None

    token.last_used_at = datetime.now(timezone.utc)
    return db.get(User, token.user_id)


def list_personal_access_tokens(db: Session, user_id: uuid.UUID) -> list[PersonalAccessToken]:
    return db.scalars(
        select(PersonalAccessToken)
        .where(PersonalAccessToken.user_id == user_id)
        .order_by(PersonalAccessToken.created_at.desc())
    ).all()


def revoke_personal_access_token(
    db: Session, user_id: uuid.UUID, token_id: uuid.UUID
) -> PersonalAccessToken | None:
    token = db.scalar(
        select(PersonalAccessToken).where(
            PersonalAccessToken.id == token_id, PersonalAccessToken.user_id == user_id
        )
    )
    if token is not None:
        token.revoked_at = datetime.now(timezone.utc)
    return token
