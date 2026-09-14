from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.job_posting import JobPosting
    from app.models.user import User


class Resume(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An uploaded resume file. A user can upload several; exactly one may
    be flagged `is_main` (enforced by the partial unique index below) — all
    review/scoring/tailored-generation features operate on that one.
    """

    __tablename__ = "resumes"
    __table_args__ = (
        Index(
            "uq_resumes_one_main_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("is_main"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    # Extracted at upload time (see app.services.resume_parser) — every LLM
    # prompt (review/score/tailor) is built from this, not the raw file.
    parsed_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_main: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped["User"] = relationship(back_populates="resumes")

    def __repr__(self) -> str:
        return f"<Resume user_id={self.user_id} filename={self.filename!r} is_main={self.is_main}>"


class ResumeReview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """General LLM-generated feedback on a resume (not tied to any specific
    job). A resume may be reviewed more than once (history kept); callers
    fetch the latest by created_at.
    """

    __tablename__ = "resume_reviews"

    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    strengths: Mapped[list] = mapped_column(JSONB, nullable=False)
    weaknesses: Mapped[list] = mapped_column(JSONB, nullable=False)
    suggestions: Mapped[list] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    raw_response: Mapped[dict | None] = mapped_column(JSONB)

    def __repr__(self) -> str:
        return f"<ResumeReview resume_id={self.resume_id}>"


class ResumeScore(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """How well a resume matches a specific JobPosting, per the LLM. A pair
    may be scored more than once (history kept); callers fetch the latest.
    """

    __tablename__ = "resume_scores"

    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_postings.id", ondelete="CASCADE"), index=True, nullable=False
    )
    overall_score: Mapped[int] = mapped_column(Integer, nullable=False)
    matched_keywords: Mapped[list] = mapped_column(JSONB, nullable=False)
    missing_keywords: Mapped[list] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    raw_response: Mapped[dict | None] = mapped_column(JSONB)

    job_posting: Mapped["JobPosting"] = relationship()

    def __repr__(self) -> str:
        return f"<ResumeScore resume_id={self.resume_id} job_posting_id={self.job_posting_id} score={self.overall_score}>"


class TailoredResume(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A resume tailored to one specific JobPosting, generated from a
    source Resume — structured content plus a rendered, downloadable
    .docx file in object storage.
    """

    __tablename__ = "tailored_resumes"

    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_postings.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # {"html": str} — an ATS-friendly HTML fragment (see
    # app.services.resume_renderer.render_html_docx), either LLM-generated
    # or uploaded directly via POST /resumes/main/tailored/upload.
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_response: Mapped[dict | None] = mapped_column(JSONB)

    job_posting: Mapped["JobPosting"] = relationship()

    def __repr__(self) -> str:
        return f"<TailoredResume resume_id={self.resume_id} job_posting_id={self.job_posting_id}>"


class CoverLetter(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A cover letter generated for one specific JobPosting, generated from
    a source Resume — structured content plus a rendered, downloadable
    .docx file in object storage.
    """

    __tablename__ = "cover_letters"

    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_postings.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # {"html": str} — an ATS-friendly HTML fragment (see
    # app.services.resume_renderer.render_html_docx), either LLM-generated
    # or uploaded directly via POST /resumes/main/cover-letter/upload.
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_response: Mapped[dict | None] = mapped_column(JSONB)

    job_posting: Mapped["JobPosting"] = relationship()

    def __repr__(self) -> str:
        return f"<CoverLetter resume_id={self.resume_id} job_posting_id={self.job_posting_id}>"
