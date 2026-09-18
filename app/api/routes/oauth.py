"""Backend half of the OAuth 2.1 authorization-code+PKCE flow the MCP
server (mcp_server/) exposes to remote clients (Claude Desktop/Code).

The MCP server's own /authorize, /token, /register routes (served by the
mcp SDK) delegate the actual persistence and user login here. Endpoints
below split into two groups:

- Server-to-server (MCP server -> here): client registration, creating a
  pending authorization request, loading/exchanging an authorization
  code, exchanging/revoking tokens. These are unauthenticated in the same
  sense a normal OAuth token endpoint is — gated by possession of a valid
  code/PKCE-verifier/refresh-token, not a shared secret.
- Browser-facing (frontend's /oauth/authorize consent page -> here):
  reading/approving/denying a pending request, gated by the user's
  existing session cookie via get_current_user.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.oauth import (
    OAuthAuthorizationCodeRead,
    OAuthAuthorizationDecisionResponse,
    OAuthAuthorizationRequestCreate,
    OAuthAuthorizationRequestCreateResponse,
    OAuthAuthorizationRequestRead,
    OAuthClientRead,
    OAuthClientRegister,
    OAuthRefreshTokenRead,
    OAuthTokenExchange,
    OAuthTokenResponse,
    OAuthTokenRevoke,
)
from app.services import oauth as oauth_service

router = APIRouter(prefix="/oauth", tags=["oauth"])


def _build_redirect_url(redirect_uri: str, **params: str | None) -> str:
    from urllib.parse import urlencode

    query = urlencode({k: v for k, v in params.items() if v is not None})
    separator = "&" if "?" in redirect_uri else "?"
    return f"{redirect_uri}{separator}{query}" if query else redirect_uri


# --- Client registration (RFC 7591) ---


@router.post("/clients", response_model=OAuthClientRead, status_code=status.HTTP_201_CREATED)
def register_client(payload: OAuthClientRegister, db: Session = Depends(get_db)) -> OAuthClientRead:
    client = oauth_service.register_client(db, payload.client_id, payload.client_name, payload.redirect_uris)
    return OAuthClientRead(
        client_id=client.client_id, client_name=client.client_name, redirect_uris=client.redirect_uris
    )


@router.get("/clients/{client_id}", response_model=OAuthClientRead)
def get_client(client_id: str, db: Session = Depends(get_db)) -> OAuthClientRead:
    client = oauth_service.get_client(db, client_id)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown client.")
    return OAuthClientRead(
        client_id=client.client_id, client_name=client.client_name, redirect_uris=client.redirect_uris
    )


# --- Authorization requests (server-to-server creation, browser-facing decision) ---


@router.post("/authorizations", response_model=OAuthAuthorizationRequestCreateResponse, status_code=status.HTTP_201_CREATED)
def create_authorization_request(
    payload: OAuthAuthorizationRequestCreate, db: Session = Depends(get_db)
) -> OAuthAuthorizationRequestCreateResponse:
    client = oauth_service.get_client(db, payload.client_id)
    if client is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown client.")
    if payload.redirect_uri not in client.redirect_uris:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unregistered redirect_uri.")

    request = oauth_service.create_authorization_request(
        db,
        client,
        payload.redirect_uri,
        payload.code_challenge,
        payload.code_challenge_method,
        payload.scopes,
        payload.resource,
        payload.state,
    )
    return OAuthAuthorizationRequestCreateResponse(request_id=request.id)


@router.get("/authorizations/{request_id}", response_model=OAuthAuthorizationRequestRead)
def read_authorization_request(
    request_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OAuthAuthorizationRequestRead:
    request = oauth_service.get_authorization_request(db, request_id)
    if request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This authorization request is invalid or has expired.")
    return OAuthAuthorizationRequestRead(
        request_id=request.id, client_name=request.client.client_name, scopes=request.scopes
    )


@router.post("/authorizations/{request_id}/approve", response_model=OAuthAuthorizationDecisionResponse)
def approve_authorization_request(
    request_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OAuthAuthorizationDecisionResponse:
    try:
        request, raw_code = oauth_service.approve_authorization_request(db, request_id, current_user)
    except oauth_service.OAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    redirect_url = _build_redirect_url(request.redirect_uri, code=raw_code, state=request.state)
    return OAuthAuthorizationDecisionResponse(redirect_url=redirect_url)


@router.post("/authorizations/{request_id}/deny", response_model=OAuthAuthorizationDecisionResponse)
def deny_authorization_request(
    request_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OAuthAuthorizationDecisionResponse:
    try:
        request = oauth_service.deny_authorization_request(db, request_id)
    except oauth_service.OAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    redirect_url = _build_redirect_url(request.redirect_uri, error="access_denied", state=request.state)
    return OAuthAuthorizationDecisionResponse(redirect_url=redirect_url)


# --- Authorization codes / tokens (server-to-server, called by the MCP server) ---


@router.get("/authorization-codes/{code}", response_model=OAuthAuthorizationCodeRead)
def read_authorization_code(code: str, db: Session = Depends(get_db)) -> OAuthAuthorizationCodeRead:
    request = oauth_service.load_authorization_code(db, code)
    if request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid or expired authorization code.")
    return OAuthAuthorizationCodeRead(
        client_id=request.client_id,
        redirect_uri=request.redirect_uri,
        code_challenge=request.code_challenge,
        code_challenge_method=request.code_challenge_method,
        scopes=request.scopes,
        resource=request.resource,
        expires_at=request.expires_at,
    )


@router.post("/authorization-codes/{code}/exchange", response_model=OAuthTokenResponse)
def exchange_authorization_code(
    code: str, payload: OAuthTokenExchange, db: Session = Depends(get_db)
) -> OAuthTokenResponse:
    request = oauth_service.load_authorization_code(db, code)
    if request is None or request.client_id != payload.client_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired authorization code.")
    client = oauth_service.get_client(db, payload.client_id)
    if client is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown client.")

    try:
        access_token, refresh_token, expires_in = oauth_service.exchange_authorization_code(db, request, client)
    except oauth_service.OAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return OAuthTokenResponse(access_token=access_token, refresh_token=refresh_token, expires_in=expires_in)


@router.get("/refresh-tokens/{token}", response_model=OAuthRefreshTokenRead)
def read_refresh_token(token: str, db: Session = Depends(get_db)) -> OAuthRefreshTokenRead:
    refresh_token = oauth_service.peek_refresh_token(db, token)
    if refresh_token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid, expired, or revoked refresh token.")
    return OAuthRefreshTokenRead(client_id=refresh_token.client_id, expires_at=refresh_token.expires_at)


@router.post("/refresh-tokens/{token}/exchange", response_model=OAuthTokenResponse)
def exchange_refresh_token(
    token: str, payload: OAuthTokenExchange, db: Session = Depends(get_db)
) -> OAuthTokenResponse:
    result = oauth_service.exchange_refresh_token(db, token, payload.client_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid, expired, or revoked refresh token.")
    access_token, refresh_token, expires_in = result
    return OAuthTokenResponse(access_token=access_token, refresh_token=refresh_token, expires_in=expires_in)


@router.post("/tokens/revoke", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(payload: OAuthTokenRevoke, db: Session = Depends(get_db)) -> None:
    oauth_service.revoke_oauth_token(db, payload.token, payload.token_type_hint)
