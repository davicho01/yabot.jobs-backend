import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token as google_id_token
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.rate_limit import RateLimitExceeded
from app.core.security import generate_token, hash_token
from app.models.auth import MagicLinkToken, PersonalAccessToken, UserSession
from app.models.enums import UserRole, UserStatus
from app.models.user import User
from app.models.user_activity import UserActiveHour


class AuthError(Exception):
    pass


def get_or_create_user(db: Session, email: str) -> User:
    email = email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        role = UserRole.ADMIN if email in settings.admin_email_set else UserRole.USER
        user = User(email=email, status=UserStatus.INVITED, role=role)
        db.add(user)
        db.flush()
    elif email in settings.admin_email_set and user.role != UserRole.ADMIN:
        # Self-heals an existing account if its email is added to
        # ADMIN_EMAILS later, without needing a manual DB edit.
        user.role = UserRole.ADMIN
    return user


def enforce_magic_link_rate_limit(
    db: Session, user: User, ip_address: str | None, now: datetime | None = None
) -> None:
    """Throttle magic-link requests before minting another one.

    Counts existing MagicLinkToken rows rather than a separate counter
    table. Per-email guards against spamming one inbox / running up SES
    cost; per-IP guards against one client cycling through many addresses.
    get_or_create_user always creates the user row regardless of whether
    the email previously existed, so raising here doesn't add any
    account-enumeration signal beyond what the endpoint already reveals.
    """
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=settings.magic_link_rate_limit_window_minutes)

    email_count = db.scalar(
        select(func.count())
        .select_from(MagicLinkToken)
        .where(MagicLinkToken.user_id == user.id, MagicLinkToken.created_at >= window_start)
    )
    if email_count >= settings.magic_link_rate_limit_max_per_email:
        raise RateLimitExceeded("Too many login requests for this email. Try again later.")

    if ip_address is not None:
        ip_count = db.scalar(
            select(func.count())
            .select_from(MagicLinkToken)
            .where(MagicLinkToken.requested_ip == ip_address, MagicLinkToken.created_at >= window_start)
        )
        if ip_count >= settings.magic_link_rate_limit_max_per_ip:
            raise RateLimitExceeded("Too many login requests. Try again later.")


def create_magic_link(db: Session, user: User, requested_ip: str | None = None) -> str:
    """Create a magic link token for the user and return the raw token
    (only ever available here — the DB stores just its hash).
    """
    raw_token = generate_token()
    db.add(
        MagicLinkToken(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.magic_link_ttl_minutes),
            requested_ip=requested_ip,
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
    _mark_logged_in(user)
    return user


def _login_code_hash(user: User, code: str) -> str:
    # Salted with the user's id: six digits alone would collide across
    # users in token_hash's unique index.
    return hash_token(f"login-code:{user.id}:{code}")


def create_login_code(db: Session, user: User, requested_ip: str | None = None) -> str:
    """A six-digit sign-in code for the user, to type in where they asked
    for it — unlike a link, which opens a new tab and loses whatever they
    were doing. Returns the code (the DB stores just its hash). It replaces
    any unused code or link they had: only the newest works."""
    now = datetime.now(timezone.utc)
    db.execute(
        update(MagicLinkToken)
        .where(MagicLinkToken.user_id == user.id, MagicLinkToken.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    while True:
        code = f"{secrets.randbelow(10**6):06d}"
        code_hash = _login_code_hash(user, code)
        # The same user drawing a code they've had before would break
        # token_hash's unique index.
        if db.scalar(select(MagicLinkToken.id).where(MagicLinkToken.token_hash == code_hash)) is None:
            break
    db.add(
        MagicLinkToken(
            user_id=user.id,
            token_hash=code_hash,
            expires_at=now + timedelta(minutes=settings.login_code_ttl_minutes),
            requested_ip=requested_ip,
        )
    )
    return code


def verify_login_code(db: Session, email: str, code: str) -> User:
    """Signs in with a code from create_login_code. A wrong code counts
    against it (settings.login_code_max_attempts) — the caller must commit
    even when this raises, or the count is rolled back."""
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        raise AuthError("That code isn't right. Check it, or send a new one.")
    # At most one: create_login_code retires the ones before it.
    token = db.scalar(
        select(MagicLinkToken).where(MagicLinkToken.user_id == user.id, MagicLinkToken.consumed_at.is_(None)).limit(1)
    )
    now = datetime.now(timezone.utc)
    if token is None or token.expires_at < now:
        raise AuthError("That code has expired. Send a new one.")
    if token.failed_attempts >= settings.login_code_max_attempts:
        raise AuthError("Too many wrong tries. Send a new code.")
    if not hmac.compare_digest(token.token_hash, _login_code_hash(user, "".join(code.split()))):
        token.failed_attempts += 1
        raise AuthError("That code isn't right. Check it, or send a new one.")

    token.consumed_at = now
    _mark_logged_in(user)
    return user


def sign_in_with_google(db: Session, credential: str) -> User:
    """Verify a Google Identity Services ID token and return its user.

    Identity is still the email: a Google sign-in for an address that
    already has an account (e.g. from a magic link) lands in that same
    account, via get_or_create_user. Safe only because Google has verified
    the address, hence the email_verified check.
    """
    audiences = [settings.google_client_id] + [
        client_id.strip() for client_id in settings.google_mobile_client_ids.split(",") if client_id.strip()
    ]
    try:
        claims = google_id_token.verify_oauth2_token(credential, GoogleAuthRequest(), audiences)
    except ValueError as exc:
        raise AuthError("Google sign-in failed. Try again.") from exc
    if not claims.get("email") or not claims.get("email_verified"):
        raise AuthError("Your Google account's email isn't verified.")

    user = get_or_create_user(db, claims["email"])
    if not user.display_name and claims.get("name"):
        user.display_name = claims["name"][:120]
    _mark_logged_in(user)
    return user


def _mark_logged_in(user: User) -> None:
    if user.status == UserStatus.INVITED:
        user.status = UserStatus.ACTIVE
    user.last_login_at = datetime.now(timezone.utc)


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


# (hour, user ids already recorded that hour) for this process, so a user's
# requests after the first one each hour skip the insert. Per instance only —
# other instances insert too, and ON CONFLICT DO NOTHING absorbs that.
_recorded_active: tuple[datetime, set[uuid.UUID]] = (datetime.min, set())


def record_active_hour(db: Session, user: User) -> None:
    """Note that `user` used the app this hour (UTC) — see UserActiveHour."""
    global _recorded_active
    hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    if _recorded_active[0] != hour:
        _recorded_active = (hour, set())
    if user.id in _recorded_active[1]:
        return

    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
    db.execute(insert(UserActiveHour).values(user_id=user.id, hour=hour).on_conflict_do_nothing())
    _recorded_active[1].add(user.id)


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
