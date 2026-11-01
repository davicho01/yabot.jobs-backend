from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class SurveyResponse(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One visitor's answers to the in-site survey (app.api.routes.survey).

    Open to signed-out visitors, so the row is keyed on `visitor_id`, a random
    id the browser generates and keeps in localStorage; `user_id` is filled in
    when they were signed in. The form is two pages and page 1 is saved on
    "Next", so a row can be partial: every answer column is nullable and
    `completed` says whether page 2 was submitted. Answers are stored as the
    slugs in app.schemas.survey (JSON lists for the multi-selects).
    """

    __tablename__ = "survey_responses"

    visitor_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    # Page 1: about the search.
    source: Mapped[str | None] = mapped_column(String(20))
    search_duration: Mapped[str | None] = mapped_column(String(20))
    industries: Mapped[list[str] | None] = mapped_column(JSON)
    role_name: Mapped[str | None] = mapped_column(String(120))
    work_arrangement: Mapped[list[str] | None] = mapped_column(JSON)

    # Page 2: about yabot.jobs.
    verification_dates_rating: Mapped[str | None] = mapped_column(String(20))
    dedup_rating: Mapped[str | None] = mapped_column(String(20))
    liked: Mapped[list[str] | None] = mapped_column(JSON)
    wanted: Mapped[list[str] | None] = mapped_column(JSON)
    comments: Mapped[str | None] = mapped_column(Text)
    # Only asked of signed-out visitors; signed-in ones are reachable via user_id.
    contact_email: Mapped[str | None] = mapped_column(String(320))
    # Whether they agreed to be contacted for a short follow-up chat: the
    # checkbox for signed-in visitors (we already have their email), or true
    # when a signed-out visitor left a contact_email.
    contact_ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped["User | None"] = relationship()

    def __repr__(self) -> str:
        return f"<SurveyResponse visitor_id={self.visitor_id!r} completed={self.completed}>"
