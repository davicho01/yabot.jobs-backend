import uuid
from datetime import datetime

from pydantic import BaseModel


class OAuthClientRegister(BaseModel):
    # The MCP SDK's RegistrationHandler mints client_id itself (uuid4, before
    # calling the provider) and expects the provider to persist that exact
    # id — not generate its own — so later lookups by the id it handed back
    # to the client resolve.
    client_id: str
    client_name: str
    redirect_uris: list[str]


class OAuthClientRead(BaseModel):
    client_id: str
    client_name: str
    redirect_uris: list[str]


class OAuthAuthorizationRequestCreate(BaseModel):
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scopes: str | None = None
    resource: str | None = None
    state: str | None = None


class OAuthAuthorizationRequestCreateResponse(BaseModel):
    request_id: uuid.UUID


class OAuthAuthorizationRequestRead(BaseModel):
    """What the frontend consent screen needs to render — deliberately
    excludes code_challenge/redirect_uri internals."""

    request_id: uuid.UUID
    client_name: str
    scopes: str | None


class OAuthAuthorizationDecisionResponse(BaseModel):
    redirect_url: str


class OAuthAuthorizationCodeRead(BaseModel):
    """What the MCP server's OAuthAuthorizationServerProvider needs to
    build an SDK AuthorizationCode — including code_challenge so the SDK
    can validate the client's PKCE verifier before exchanging."""

    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scopes: str | None
    resource: str | None
    expires_at: datetime


class OAuthRefreshTokenRead(BaseModel):
    """What the MCP server's provider needs to build an SDK RefreshToken —
    read-only, does not consume/rotate the token."""

    client_id: str
    expires_at: datetime


class OAuthTokenExchange(BaseModel):
    client_id: str


class OAuthTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class OAuthTokenRevoke(BaseModel):
    token: str
    token_type_hint: str | None = None
