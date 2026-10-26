import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserActiveDay(Base):
    """One row per user per UTC day they made an authenticated request —
    the visit history the admin "Returning users" chart counts. Session and
    token `last_used_at` only keep the latest visit, so they can't answer
    "who came back on day D"; this table can, from the day it shipped on.
    """

    __tablename__ = "user_active_days"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
