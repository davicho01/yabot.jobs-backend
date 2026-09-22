from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import UserRole, UserStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.api_key import UserApiKey
    from app.models.auth import MagicLinkToken, PersonalAccessToken, UserSession
    from app.models.job_application import UserJobApplication
    from app.models.oauth import OAuthAuthorizationRequest, OAuthRefreshToken
    from app.models.resume import Resume
    from app.models.saved_search import SavedSearch


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A person using the app. Identity is the email — there are no
    passwords; access is granted via emailed magic links (see
    MagicLinkToken) and then persisted via UserSession (remember-me).
    """

    __tablename__ = "users"

    # Stored lowercased/trimmed by the application layer before insert.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(
        String(20), default=UserStatus.INVITED, nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), default=UserRole.USER, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Whether to email a digest for this user's saved searches (see
    # app.services.saved_search_alerts.sweep_saved_searches, the only
    # reader). Defaults on: saving a search is itself an opt-in signal, and
    # this is the one place to turn it back off without deleting every
    # saved search individually.
    email_alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    magic_link_tokens: Mapped[list["MagicLinkToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    personal_access_tokens: Mapped[list["PersonalAccessToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    api_keys: Mapped[list["UserApiKey"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    job_applications: Mapped[list["UserJobApplication"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    resumes: Mapped[list["Resume"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    saved_searches: Mapped[list["SavedSearch"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    oauth_authorization_requests: Mapped[list["OAuthAuthorizationRequest"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    oauth_refresh_tokens: Mapped[list["OAuthRefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User {self.email!r} status={self.status!r}>"
