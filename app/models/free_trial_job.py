from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class FreeTrialJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A job a user unlocked with one of their free evaluations (see
    app.services.ai_access.job_llm_credentials). Once a job is here, every
    AI feature for it — score, breakdown, tailored resume, cover letter,
    interview prep — runs free for that user; User.free_evaluations_used
    counts these rows.
    """

    __tablename__ = "free_trial_jobs"
    __table_args__ = (UniqueConstraint("user_id", "job_posting_id", name="uq_free_trial_jobs_user_job"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False
    )
