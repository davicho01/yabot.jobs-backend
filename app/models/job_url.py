from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ScanStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.job_posting import JobPosting
    from app.models.user import User


class JobPostingUrl(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A deduplicated, app-wide registry of job posting URLs.

    Any user can submit a URL another user already submitted — this table
    is the single shared identity for that URL, so scraped data
    (JobPosting) is fetched/stored once and reused by everyone, instead
    of re-scanned per user.
    """

    __tablename__ = "job_posting_urls"

    # Original URL as submitted, kept for display/debugging.
    url: Mapped[str] = mapped_column(Text, nullable=False)
    # Canonicalized form (lowercased host, stripped tracking params/fragment)
    # used to compute url_hash so trivial variants dedupe to one row.
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    # SHA-256 of normalized_url — URLs are unbounded length, so we index
    # this fixed-width hash rather than the url text itself.
    url_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    domain: Mapped[str] = mapped_column(String(255), index=True, nullable=False)

    scan_status: Mapped[str] = mapped_column(
        String(20), default=ScanStatus.PENDING, nullable=False
    )
    scan_error: Mapped[str | None] = mapped_column(Text)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Attribution only ("who first brought this URL in") — not ownership.
    # The URL and its scraped postings are shared app-wide.
    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # Which CrawlSource discovered this URL (null for user-submitted ones).
    # SET NULL rather than CASCADE: deleting/rejecting a source shouldn't
    # take down listings that were genuinely found there.
    crawl_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crawl_sources.id", ondelete="SET NULL"), index=True
    )

    submitted_by: Mapped["User | None"] = relationship()
    postings: Mapped[list["JobPosting"]] = relationship(
        back_populates="url", cascade="all, delete-orphan", order_by="JobPosting.scanned_at.desc()"
    )

    def __repr__(self) -> str:
        return f"<JobPostingUrl domain={self.domain!r} status={self.scan_status!r}>"
