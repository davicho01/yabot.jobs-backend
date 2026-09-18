"""Service layer backing the MCP server's OAuth 2.1 authorization-code
flow (see app/api/routes/oauth.py). Reuses the same opaque-token,
hash-at-rest scheme as app.services.auth (generate_token/hash_token) and
the PersonalAccessToken model itself — the OAuth "access token" a client
ends up holding *is* a PersonalAccessToken, just short-lived and minted
through this exchange instead of the user-facing /auth/tokens endpoint.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import generate_token, hash_token
from app.models.auth import PersonalAccessToken
from app.models.oauth import OAuthAuthorizationRequest, OAuthClient, OAuthRefreshToken
from app.models.user import User

# How long a pending authorization request stays valid before the user
# must have logged in and approved it (covers the round trip through the
# magic-link email).
PENDING_REQUEST_TTL_MINUTES = 10
# How long an *issued* authorization code stays redeemable once approved.
AUTHORIZATION_CODE_TTL_SECONDS = 120
ACCESS_TOKEN_TTL = timedelta(hours=1)
REFRESH_TOKEN_TTL = timedelta(days=90)


class OAuthError(Exception):
    pass


# --- Clients (RFC 7591 dynamic client registration) ---


def register_client(db: Session, client_id: str, client_name: str, redirect_uris: list[str]) -> OAuthClient:
    client = OAuthClient(client_id=client_id, client_name=client_name, redirect_uris=redirect_uris)
    db.add(client)
    db.flush()
    return client


def get_client(db: Session, client_id: str) -> OAuthClient | None:
    return db.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id))


# --- Authorization requests / codes ---


def create_authorization_request(
    db: Session,
    client: OAuthClient,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    scopes: str | None,
    resource: str | None,
    state: str | None,
) -> OAuthAuthorizationRequest:
    request = OAuthAuthorizationRequest(
        client_id=client.client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scopes=scopes,
        resource=resource,
        state=state,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=PENDING_REQUEST_TTL_MINUTES),
    )
    db.add(request)
    db.flush()
    return request


def get_authorization_request(db: Session, request_id: uuid.UUID) -> OAuthAuthorizationRequest | None:
    request = db.get(OAuthAuthorizationRequest, request_id)
    if request is None or request.consumed_at is not None or request.expires_at < datetime.now(timezone.utc):
        return None
    return request


def approve_authorization_request(
    db: Session, request_id: uuid.UUID, user: User
) -> tuple[OAuthAuthorizationRequest, str]:
    """Approve a pending request on behalf of `user`, who must already be
    logged in (session cookie) on the frontend consent screen. Returns
    the request plus the raw authorization code — only ever available
    here, at issuance.
    """
    request = get_authorization_request(db, request_id)
    if request is None:
        raise OAuthError("This authorization request is invalid or has expired.")
    if request.user_id is not None:
        raise OAuthError("This authorization request has already been decided.")

    raw_code = generate_token()
    request.user_id = user.id
    request.code_hash = hash_token(raw_code)
    request.expires_at = datetime.now(timezone.utc) + timedelta(seconds=AUTHORIZATION_CODE_TTL_SECONDS)
    return request, raw_code


def deny_authorization_request(db: Session, request_id: uuid.UUID) -> OAuthAuthorizationRequest:
    request = get_authorization_request(db, request_id)
    if request is None:
        raise OAuthError("This authorization request is invalid or has expired.")
    request.consumed_at = datetime.now(timezone.utc)
    return request


def load_authorization_code(db: Session, raw_code: str) -> OAuthAuthorizationRequest | None:
    code_hash = hash_token(raw_code)
    request = db.scalar(select(OAuthAuthorizationRequest).where(OAuthAuthorizationRequest.code_hash == code_hash))
    if request is None or request.consumed_at is not None:
        return None
    if request.expires_at < datetime.now(timezone.utc):
        return None
    return request


def _mint_access_token(db: Session, user: User, client_name: str) -> tuple[PersonalAccessToken, str]:
    raw_token = generate_token()
    token = PersonalAccessToken(
        user_id=user.id,
        label=f"OAuth: {client_name}",
        token_hash=hash_token(raw_token),
        expires_at=datetime.now(timezone.utc) + ACCESS_TOKEN_TTL,
    )
    db.add(token)
    db.flush()
    return token, raw_token


def _mint_refresh_token(
    db: Session, user: User, client: OAuthClient, access_token: PersonalAccessToken
) -> str:
    raw_token = generate_token()
    db.add(
        OAuthRefreshToken(
            token_hash=hash_token(raw_token),
            client_id=client.client_id,
            user_id=user.id,
            access_token_id=access_token.id,
            expires_at=datetime.now(timezone.utc) + REFRESH_TOKEN_TTL,
        )
    )
    return raw_token


def exchange_authorization_code(
    db: Session, request: OAuthAuthorizationRequest, client: OAuthClient
) -> tuple[str, str, int]:
    """Consume an approved authorization request and mint a fresh
    (access_token, refresh_token, expires_in_seconds) triple. Caller must
    have already validated the code via load_authorization_code (and PKCE,
    which the MCP SDK does itself before calling this).
    """
    if request.consumed_at is not None or request.expires_at < datetime.now(timezone.utc):
        raise OAuthError("This authorization code is invalid or has expired.")
    if request.user_id is None:
        raise OAuthError("This authorization request was never approved.")

    user = db.get(User, request.user_id)
    if user is None:
        raise OAuthError("This authorization request was never approved.")

    request.consumed_at = datetime.now(timezone.utc)
    access_token, raw_access_token = _mint_access_token(db, user, client.client_name)
    raw_refresh_token = _mint_refresh_token(db, user, client, access_token)
    return raw_access_token, raw_refresh_token, int(ACCESS_TOKEN_TTL.total_seconds())


def peek_refresh_token(db: Session, raw_refresh_token: str) -> OAuthRefreshToken | None:
    """Look up a refresh token without consuming it — the MCP SDK's token
    handler calls this (load_refresh_token) to validate before deciding to
    call exchange_refresh_token, same two-step shape as authorization codes.
    """
    token_hash = hash_token(raw_refresh_token)
    refresh_token = db.scalar(select(OAuthRefreshToken).where(OAuthRefreshToken.token_hash == token_hash))
    if refresh_token is None or refresh_token.revoked_at is not None:
        return None
    if refresh_token.expires_at < datetime.now(timezone.utc):
        return None
    return refresh_token


def exchange_refresh_token(db: Session, raw_refresh_token: str, client_id: str) -> tuple[str, str, int] | None:
    """Validate and rotate a refresh token: revokes the old refresh token
    and its paired access token, mints a fresh pair. Returns None if the
    refresh token is invalid, expired, revoked, or doesn't belong to
    `client_id`.
    """
    token_hash = hash_token(raw_refresh_token)
    refresh_token = db.scalar(select(OAuthRefreshToken).where(OAuthRefreshToken.token_hash == token_hash))
    if refresh_token is None or refresh_token.revoked_at is not None:
        return None
    if refresh_token.client_id != client_id:
        return None
    if refresh_token.expires_at < datetime.now(timezone.utc):
        return None

    user = db.get(User, refresh_token.user_id)
    client = db.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id))
    if user is None or client is None:
        return None

    now = datetime.now(timezone.utc)
    refresh_token.revoked_at = now
    old_access_token = db.get(PersonalAccessToken, refresh_token.access_token_id)
    if old_access_token is not None:
        old_access_token.revoked_at = now

    access_token, raw_access_token = _mint_access_token(db, user, client.client_name)
    raw_new_refresh_token = _mint_refresh_token(db, user, client, access_token)
    return raw_access_token, raw_new_refresh_token, int(ACCESS_TOKEN_TTL.total_seconds())


def revoke_oauth_token(db: Session, raw_token: str, token_type_hint: str | None) -> None:
    """Revoke a token per RFC 7009 — no-ops silently if the token is
    unknown/already revoked, and (per the OAuthAuthorizationServerProvider
    contract) revokes the paired credential too: revoking a refresh token
    also revokes its access token, and vice versa.
    """
    token_hash = hash_token(raw_token)
    now = datetime.now(timezone.utc)

    check_refresh_first = token_type_hint != "access_token"
    order = (
        (_revoke_refresh_token_by_hash, _revoke_access_token_by_hash)
        if check_refresh_first
        else (_revoke_access_token_by_hash, _revoke_refresh_token_by_hash)
    )
    for revoke in order:
        if revoke(db, token_hash, now):
            return


def _revoke_access_token_by_hash(db: Session, token_hash: str, now: datetime) -> bool:
    token = db.scalar(select(PersonalAccessToken).where(PersonalAccessToken.token_hash == token_hash))
    if token is None:
        return False
    token.revoked_at = now
    refresh_token = db.scalar(select(OAuthRefreshToken).where(OAuthRefreshToken.access_token_id == token.id))
    if refresh_token is not None:
        refresh_token.revoked_at = now
    return True


def _revoke_refresh_token_by_hash(db: Session, token_hash: str, now: datetime) -> bool:
    refresh_token = db.scalar(select(OAuthRefreshToken).where(OAuthRefreshToken.token_hash == token_hash))
    if refresh_token is None:
        return False
    refresh_token.revoked_at = now
    access_token = db.get(PersonalAccessToken, refresh_token.access_token_id)
    if access_token is not None:
        access_token.revoked_at = now
    return True
