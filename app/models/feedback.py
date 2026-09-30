from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import FeedbackKind, FeedbackStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class Feedback(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Something a signed-in user sent from the in-app "Feedback" button or
    the Help page's contact form (see app.api.routes.feedback). Admins triage
    it from GET /admin/feedback by moving `status` from NEW to READ/RESOLVED.
    """

    __tablename__ = "feedback"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), default=FeedbackKind.OTHER, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional 1-5 rating of the app overall.
    rating: Mapped[int | None] = mapped_column(Integer)
    # The page the user was on when they opened the form, so a bug report
    # doesn't have to describe where it happened.
    page_url: Mapped[str | None] = mapped_column(String(2048))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(20), default=FeedbackStatus.NEW, index=True, nullable=False)

    user: Mapped["User"] = relationship()

    def __repr__(self) -> str:
        return f"<Feedback user_id={self.user_id} kind={self.kind!r} status={self.status!r}>"
