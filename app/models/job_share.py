from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JobShare(Base):
    """A job link someone signed in shared from the app (POST /shares). Its
    id is random, made in the browser at the tap so the share sheet opens at
    once, and rides on the link as ?ref=share&sid=<id>; a sign-up that came
    by it records the id (User.signup_share_id). Shares made signed out, or
    from a static job page, carry an id too but have no row: nobody to
    credit."""

    __tablename__ = "job_shares"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_url_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_posting_urls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    method: Mapped[str] = mapped_column(String(20), nullable=False)  # share_sheet | copy_link
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=func.now(), nullable=False
    )
