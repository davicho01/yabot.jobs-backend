from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class DeviceToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An Expo push token for one install of the mobile app. Unique on the
    token itself: signing in as someone else on the same phone moves the
    token to the new user rather than leaving the old user pushed to it.
    Removed on sign-out, or when Expo reports it as no longer registered
    (see app.services.push).
    """

    __tablename__ = "device_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(10), nullable=False)  # "ios" | "android"
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
