from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class SavedSearch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's saved job-board filter set — one column per GET /jobs query
    param that's worth persisting (everything JobFilters, src/api/jobs.ts,
    has except `page`). Re-run on a schedule (see saved_search_alerts.py) to
    email the user when a new posting matches; last_alerted_at is how that
    script tells "new since I last checked" from "already told them about
    this one".
    """

    __tablename__ = "saved_searches"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # A short label for the searches list — free text, not derived from the
    # filters below, so renaming it doesn't require reverse-engineering one
    # back into words. Optional: a blank one just falls back to "Search" in
    # the UI rather than forcing everyone to type something.
    name: Mapped[str | None] = mapped_column(String(120))

    q: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    metro: Mapped[str | None] = mapped_column(String(60))
    radius: Mapped[int | None] = mapped_column(Integer)
    company: Mapped[str | None] = mapped_column(String(255))
    posted_within_days: Mapped[int | None] = mapped_column(Integer)
    workplace_type: Mapped[str | None] = mapped_column(String(20))
    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)

    # Null until the first alert sweep runs for this search; the sweep only
    # ever looks at postings scanned after this (or after created_at, the
    # first time), so a search never re-alerts about something it already
    # sent, and a brand-new search doesn't dump every existing match at once.
    last_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="saved_searches")

    def __repr__(self) -> str:
        return f"<SavedSearch user_id={self.user_id} name={self.name!r}>"
