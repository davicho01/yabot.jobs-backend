from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class SubscriptionEvaluationJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A job a subscriber unlocked with one of their period's evaluations
    (see app.services.ai_access.job_llm_credentials). Scoped by period_end,
    unlike FreeTrialJob's lifetime unlock, since the subscription cap resets
    every billing period; User.subscription_evaluations_used counts rows
    matching the user's current period_end.
    """

    __tablename__ = "subscription_evaluation_jobs"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "job_posting_id", "period_end", name="uq_subscription_eval_jobs_user_job_period"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False
    )
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
