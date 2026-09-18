from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, JSON, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.auth import PersonalAccessToken
    from app.models.user import User


class OAuthClient(Base, UUIDPrimaryKeyMixin):
    """An OAuth client dynamically registered against this server (RFC
    7591), e.g. Claude Desktop/Code connecting to the MCP server. Public
    client only — no secret is stored, since MCP clients authenticate the
    authorization-code exchange with PKCE instead.
    """

    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    client_name: Mapped[str] = mapped_column(String(120), nullable=False)
    redirect_uris: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OAuthAuthorizationRequest(Base, UUIDPrimaryKeyMixin):
    """One authorization-code request/grant, covering both its 'pending'
    state (created when the MCP server's /authorize is hit, before the
    user has logged in or approved anything — user_id/code_hash are null)
    and its 'issued' state (after the user approves on the frontend
    consent screen — user_id and code_hash get set, expires_at is
    shortened to a couple of minutes). `id` doubles as the opaque
    'request_id' the frontend consent page looks this row up by.
    """

    __tablename__ = "oauth_authorization_requests"

    client_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("oauth_clients.client_id", ondelete="CASCADE"), index=True, nullable=False
    )
    redirect_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    code_challenge_method: Mapped[str] = mapped_column(String(16), nullable=False)
    scopes: Mapped[str | None] = mapped_column(String(255))
    resource: Mapped[str | None] = mapped_column(String(2048))
    state: Mapped[str | None] = mapped_column(String(512))
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    client: Mapped["OAuthClient"] = relationship()
    user: Mapped["User | None"] = relationship(back_populates="oauth_authorization_requests")


class OAuthRefreshToken(Base, UUIDPrimaryKeyMixin):
    """A rotating refresh token paired 1:1 with the PersonalAccessToken
    it can be exchanged to renew. Same hash-at-rest handling as the other
    auth tokens (see app.models.auth).
    """

    __tablename__ = "oauth_refresh_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    client_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("oauth_clients.client_id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    access_token_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("personal_access_tokens.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    client: Mapped["OAuthClient"] = relationship()
    user: Mapped["User"] = relationship(back_populates="oauth_refresh_tokens")
    access_token: Mapped["PersonalAccessToken"] = relationship()
