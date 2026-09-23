from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ApplicationStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.job_posting import JobPosting
    from app.models.resume import CoverLetter, ResumeScore, TailoredResume, TailoredResumeScore
    from app.models.user import User


class UserJobApplication(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-user tracking state for a job posting: whether they've saved
    it, applied, are interviewing, etc. This is the many-to-many join
    between users and the shared JobPosting table.
    """

    __tablename__ = "user_job_applications"
    __table_args__ = (
        UniqueConstraint("user_id", "job_posting_id", name="uq_user_job_applications_user_job"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_postings.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default=ApplicationStatus.SAVED, nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    # Hides the application from the default list without losing its
    # history/notes/status — distinct from `status` (withdrawn/rejected
    # still describe an active pipeline stage; archiving just declutters).
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # A self-set "remind me about this one" date — a day, not a timestamp:
    # this is "follow up sometime on the 5th", not a specific moment. Null
    # means no reminder wanted. See app.services.follow_up_reminders, the
    # only reader.
    follow_up_at: Mapped[date | None] = mapped_column(Date)

    # Which of the user's resumes the apply page's resume picker is set to
    # for this application — persisted server-side (rather than left as
    # local component state) so it survives a page refresh, not just the
    # candidate's in-memory pick. Null means "no explicit pick", which the
    # frontend falls back to is_main for, same as before this existed.
    # SET NULL on delete: removing the resume shouldn't take the
    # application down with it, just fall back to main again.
    selected_resume_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="SET NULL")
    )
    # Set once a reminder email has actually gone out for the *current*
    # follow_up_at — update_application resets this to null whenever
    # follow_up_at itself changes (a new date means a new reminder is
    # wanted), so this only ever suppresses re-sending for a date already
    # reminded about, never a freshly (re)set one.
    follow_up_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Pointers to the most recent row in each history table (score/tailored
    # resume/cover letter can each be regenerated, so those tables keep
    # every version) — kept in sync at write time by the resumes routes, so
    # the applications list can show current state in one query instead of
    # re-deriving "latest" per job on every read.
    latest_score_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resume_scores.id", ondelete="SET NULL")
    )
    latest_tailored_resume_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tailored_resumes.id", ondelete="SET NULL")
    )
    latest_cover_letter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cover_letters.id", ondelete="SET NULL")
    )
    latest_tailored_resume_score_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tailored_resume_scores.id", ondelete="SET NULL")
    )

    user: Mapped["User"] = relationship(back_populates="job_applications")
    job_posting: Mapped["JobPosting"] = relationship(back_populates="applications")
    latest_score: Mapped["ResumeScore | None"] = relationship(foreign_keys=[latest_score_id])
    latest_tailored_resume: Mapped["TailoredResume | None"] = relationship(foreign_keys=[latest_tailored_resume_id])
    latest_cover_letter: Mapped["CoverLetter | None"] = relationship(foreign_keys=[latest_cover_letter_id])
    latest_tailored_resume_score: Mapped["TailoredResumeScore | None"] = relationship(
        foreign_keys=[latest_tailored_resume_score_id]
    )

    @property
    def best_score(self) -> int | None:
        """The higher of the original fitness score and the tailored-resume
        score, whichever is set — the applications list shows one combined
        badge rather than two separate scores.
        """
        scores = [
            s.overall_score for s in (self.latest_score, self.latest_tailored_resume_score) if s is not None
        ]
        return max(scores) if scores else None

    def __repr__(self) -> str:
        return f"<UserJobApplication user_id={self.user_id} status={self.status!r}>"
