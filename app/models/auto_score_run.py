from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class AutoScoreRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One background "score → tailor → score tailored" pass over a
    (user, resume, job) — see app.services.auto_score, which runs it on the
    scan worker. Lives in its own table rather than on UserJobApplication
    since a run can start (and end below threshold) for a job the user has
    no application for; one is only created/marked ready if the run's best
    score clears the bar. Each run keeps its row, so the latest per
    user+job is the current state.
    """

    __tablename__ = "auto_score_runs"
    __table_args__ = (Index("ix_auto_score_runs_user_job_created", "user_id", "job_posting_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False
    )
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False
    )
    # See app.models.enums.AutoScoreStatus.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    resume_score_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resume_scores.id", ondelete="SET NULL")
    )
    tailored_resume_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tailored_resumes.id", ondelete="SET NULL")
    )
    tailored_score_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tailored_resume_scores.id", ondelete="SET NULL")
    )
    resume_score: Mapped[int | None] = mapped_column(Integer)
    tailored_score: Mapped[int | None] = mapped_column(Integer)
    best_score: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
