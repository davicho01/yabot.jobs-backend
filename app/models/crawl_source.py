from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, Index, Integer, String, Text, true
from sqlalchemy.dialects.postgresql import JSONB
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

    # The company's official name: every job from this source shows it (or
    # one of sub_brands) — the official company site is the authority on
    # who owns a job. See app.services.company_names.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Where `name` came from: "placeholder" (an unconfirmed slug/hostname
    # label from crawl_sources._company_name — the first scan with a usable
    # company name replaces it), "auto" (set from evidence) or "manual" (set
    # by an admin; nothing automatic ever changes it).
    name_source: Mapped[str] = mapped_column(String(12), default="auto", server_default="auto", nullable=False)
    # Brands this source's jobs show instead of `name` when the job page
    # names one ("HomeGoods", "Marshalls" on TJX's site).
    sub_brands: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]", nullable=False)
    # The company's own careers site (true) vs a job board or aggregator
    # (false): official sources own their jobs, are crawled first, and their
    # copy of a job wins over copies found elsewhere.
    is_official: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    ats_type: Mapped[str | None] = mapped_column(String(20))
    board_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=CrawlSourceStatus.ACTIVE, nullable=False)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    # Set by crawl_dispatcher.py right before publishing this source's
    # wake-up, cleared by crawl_worker.py once that crawl finishes (success
    # or a handled failure) — so a source with an outstanding, not-yet-
    # processed wake-up is skipped on the next dispatch cycle instead of
    # getting a duplicate published on top of it. A claim older than
    # settings.crawl_claim_ttl_seconds is treated as abandoned (a dead
    # worker, a lost message) and the source becomes dispatchable again —
    # same reasoning as JobPostingUrl.scan_claimed_at (see
    # app.services.scan_claims), just one level up: this stops the
    # *dispatch* messages themselves from piling up, not the scans within
    # one already-dispatched crawl.
    crawl_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Politeness cap: how many of this source's job pages may be fetched at
    # the same time (see app.services.scan_claims). Lower it for a site that
    # is sensitive to load, raise it (max 5) for one that can take more.
    max_concurrent_scans: Mapped[int] = mapped_column(
        Integer,
        default=DEFAULT_MAX_CONCURRENT_SCANS,
        server_default=str(DEFAULT_MAX_CONCURRENT_SCANS),
        nullable=False,
    )
    # Generic, adapter-agnostic coverage monitoring (see
    # app.services.coverage_monitor.update_coverage) — catches a source's
    # discovered-URL count silently regressing (a broken adapter, an ATS
    # API/rate-limit change, ...) for ANY platform, not just a specific bug.
    # It can't catch a source that's been under-counting since its very
    # first crawl (nothing to regress from) — that class of bug is prevented
    # at the adapter level instead.
    coverage_last_count: Mapped[int | None] = mapped_column(Integer)
    coverage_baseline: Mapped[float | None] = mapped_column(Float)
    coverage_sample_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    coverage_low_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    coverage_flagged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<CrawlSource name={self.name!r} ats_type={self.ats_type!r} board_url={self.board_url!r} status={self.status!r}>"
