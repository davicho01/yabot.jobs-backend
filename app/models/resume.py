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
    A row starts out as a quick score (category_scores empty) and may later
    be mutated in place — not superseded by a new row — once the candidate
    requests the comprehensive category breakdown; see
    app.api.routes.resumes's evaluation endpoint.
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
    # Per-rubric-category breakdown — see app.services.resume_llm._CATEGORY_MAX
    # for the fixed category list and point ranges. Always 4 entries.
    category_scores: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Informational only — a heads-up about real-world overqualification/
    # age-perception bias risk. Never factored into overall_score or
    # category_scores, which stay purely merit-based.
    overqualification_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_response: Mapped[dict | None] = mapped_column(JSONB)

    job_posting: Mapped["JobPosting"] = relationship()

    def __repr__(self) -> str:
        return f"<ResumeScore resume_id={self.resume_id} job_posting_id={self.job_posting_id} score={self.overall_score}>"


class TailoredResumeScore(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """How well a TailoredResume matches the JobPosting it was generated
    for, per the LLM — the tailored-resume analogue of ResumeScore. A
    tailored resume may be scored more than once (history kept); callers
    fetch the latest. A row starts out as a quick score (category_scores
    empty) and may later be mutated in place — not superseded by a new row
    — once the candidate requests the comprehensive category breakdown; see
    app.api.routes.resumes's evaluation endpoint.
    """

    __tablename__ = "tailored_resume_scores"

    tailored_resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tailored_resumes.id", ondelete="CASCADE"), index=True, nullable=False
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
    # Per-rubric-category breakdown — see app.services.resume_llm._CATEGORY_MAX
    # for the fixed category list and point ranges. Always 4 entries.
    category_scores: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Informational only — a heads-up about real-world overqualification/
    # age-perception bias risk. Never factored into overall_score or
    # category_scores, which stay purely merit-based.
    overqualification_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_response: Mapped[dict | None] = mapped_column(JSONB)

    job_posting: Mapped["JobPosting"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<TailoredResumeScore tailored_resume_id={self.tailored_resume_id} "
            f"job_posting_id={self.job_posting_id} score={self.overall_score}>"
        )


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
    # app.schemas.resume.TailoredResumeUpload's shape — {"summary": str,
    # "sections": [{"heading": str, "bullets": [str, ...]}, ...]} — either
    # LLM-generated or uploaded directly via POST /resumes/main/tailored/upload.
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_response: Mapped[dict | None] = mapped_column(JSONB)

    job_posting: Mapped["JobPosting"] = relationship()

    def __repr__(self) -> str:
        return f"<TailoredResume resume_id={self.resume_id} job_posting_id={self.job_posting_id}>"


class InterviewPrep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """LLM-generated interview prep for a resume against a specific
    JobPosting: likely questions (each with a note on how this candidate
    specifically should answer it), talking points worth proactively
    raising, and questions worth asking the interviewer. Inline content,
    like ResumeScore — no rendered file, nothing to download. A pair may be
    generated more than once (history kept, same as ResumeScore); callers
    fetch the latest.
    """

    __tablename__ = "interview_preps"

    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_postings.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # app.schemas.resume.InterviewPrepContent's shape — {"likely_questions":
    # [{"question": str, "category": str, "approach": str}, ...],
    # "talking_points": [str, ...], "questions_to_ask": [str, ...]}.
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    raw_response: Mapped[dict | None] = mapped_column(JSONB)

    job_posting: Mapped["JobPosting"] = relationship()

    def __repr__(self) -> str:
        return f"<InterviewPrep resume_id={self.resume_id} job_posting_id={self.job_posting_id}>"


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
    # app.schemas.resume.CoverLetterUpload's shape — {"greeting": str,
    # "body_paragraphs": [str, ...], "closing": str} — either LLM-generated
    # or uploaded directly via POST /resumes/main/cover-letter/upload.
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_response: Mapped[dict | None] = mapped_column(JSONB)

    job_posting: Mapped["JobPosting"] = relationship()

    def __repr__(self) -> str:
        return f"<CoverLetter resume_id={self.resume_id} job_posting_id={self.job_posting_id}>"
