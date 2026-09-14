from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import CrawlSourceStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class CrawlSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A company/board the daily discovery crawler checks for new postings.

    Discovery hits the ATS's own public jobs-list API (see
    app.services.ats_adapters) to get a list of job URLs; each URL is then
    handed to the normal get_or_create_job_posting flow, so a source is
    purely "what to check", not "what was found" — the found postings live
    in JobPostingUrl/JobPosting like any other submission.

    A row can also represent a *not-yet-supported* board: when a submitted
    job URL doesn't match any implemented ATS platform
    (app.services.ats_adapters.detect_ats_source), it's recorded here with
    ats_type/board_token left null and detected_domain set instead, status
    "pending" — a queue of platforms worth investigating. Once someone
    verifies and implements an adapter, ats_type/board_token get filled in
    and status flips to "active"; if a platform turns out to have no
    viable public API, status flips to "rejected" instead.
    """

    __tablename__ = "crawl_sources"
    __table_args__ = (
        Index(
            "uq_crawl_sources_ats_type_board_token",
            "ats_type",
            "board_token",
            unique=True,
            postgresql_where=text("ats_type IS NOT NULL"),
        ),
        Index(
            "uq_crawl_sources_detected_domain",
            "detected_domain",
            unique=True,
            postgresql_where=text("ats_type IS NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ats_type: Mapped[str | None] = mapped_column(String(20))
    board_token: Mapped[str | None] = mapped_column(String(255))
    detected_domain: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default=CrawlSourceStatus.ACTIVE, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_job_count: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<CrawlSource name={self.name!r} ats_type={self.ats_type!r} board_token={self.board_token!r} status={self.status!r}>"
