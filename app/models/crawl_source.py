from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import CrawlSourceStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

DEFAULT_MAX_CONCURRENT_SCANS = 3
MIN_CONCURRENT_SCANS = 1
MAX_CONCURRENT_SCANS = 5


class CrawlSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A company/board the daily discovery crawler checks for new postings.

    Discovery hits the ATS's own public jobs-list API (see
    app.services.ats_adapters) to get a list of job URLs; each URL is then
    handed to the normal get_or_create_job_posting flow, so a source is
    purely "what to check", not "what was found" — the found postings live
    in JobPostingUrl/JobPosting like any other submission.

    board_url is the single source of truth for "which board" — ats_type
    and any other identifier a given platform's adapter needs (see
    app.services.ats_adapters.list_job_urls) are derived from it on demand
    rather than stored separately, since they're fully recoverable from the
    URL for every supported platform (see
    app.services.ats_adapters.detect_ats_source /
    app.services.ats_adapters.board_url_for_key).

    A row can also represent a *not-yet-supported* board: when a submitted
    job URL doesn't match any implemented ATS platform, it's recorded here
    with ats_type left null and board_url set to the domain root, status
    "pending" — a queue of platforms worth investigating. Once someone
    verifies and implements an adapter, ats_type/board_url get filled in
    and status flips to "active"; if a platform turns out to have no
    viable public API, status flips to "rejected" instead.
    """

    __tablename__ = "crawl_sources"
    __table_args__ = (
        Index("uq_crawl_sources_board_url", "board_url", unique=True),
        CheckConstraint(
            f"max_concurrent_scans BETWEEN {MIN_CONCURRENT_SCANS} AND {MAX_CONCURRENT_SCANS}",
            name="max_concurrent_scans_range",
        ),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ats_type: Mapped[str | None] = mapped_column(String(20))
    board_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=CrawlSourceStatus.ACTIVE, nullable=False)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    # Politeness cap: how many of this source's job pages may be fetched at
    # the same time (see app.services.scan_claims). Lower it for a site that
    # is sensitive to load, raise it (max 5) for one that can take more.
    max_concurrent_scans: Mapped[int] = mapped_column(
        Integer,
        default=DEFAULT_MAX_CONCURRENT_SCANS,
        server_default=str(DEFAULT_MAX_CONCURRENT_SCANS),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<CrawlSource name={self.name!r} ats_type={self.ats_type!r} board_url={self.board_url!r} status={self.status!r}>"
