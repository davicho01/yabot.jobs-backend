from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import EmploymentType, ScanStatus, WorkplaceType
from app.models.mixins import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.job_application import UserJobApplication
    from app.models.job_url import JobPostingUrl


class JobPosting(UUIDPrimaryKeyMixin, Base):
    """The job info extracted from a JobPostingUrl (title, company, comp,
    description, ...). Shared app-wide via the URL it belongs to.

    One row per URL (url_id is unique) — a rescan updates this row in place
    rather than adding a new one; `scanned_at` just records when the
    current data was last fetched.
    """

    __tablename__ = "job_postings"
    __table_args__ = (
        # Serves the metro-area filter (`metros @> '["41620"]'`) — see
        # app.api.routes.jobs.list_job_urls.
        Index("ix_job_postings_metros", "metros", postgresql_using="gin", postgresql_ops={"metros": "jsonb_path_ops"}),
    )

    url_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_posting_urls.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )

    title: Mapped[str | None] = mapped_column(String(255))
    company_name: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    # The individual locations parsed out of `location` (which adapters join
    # with "; " when a posting lists several) — what location filtering and
    # suggestions actually match against. Kept in sync by _upsert_posting via
    # app.services.job_locations.split_locations; see that module for the rules.
    locations: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Census metro/micro area (CBSA) codes those locations fall in, so postings
    # can be searched by area ("Salt Lake City") however the place was spelled.
    # Derived from `locations` by app.services.geo.resolve_metros in
    # _upsert_posting; entries it can't place (states, "Remote", non-US,
    # facility names) simply contribute nothing.
    metros: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    workplace_type: Mapped[str] = mapped_column(String(20), default=WorkplaceType.UNKNOWN, nullable=False)
    employment_type: Mapped[str] = mapped_column(String(20), default=EmploymentType.UNKNOWN, nullable=False)

    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    salary_currency: Mapped[str | None] = mapped_column(String(3))

    description: Mapped[str | None] = mapped_column(Text)
    # Structured extras an extractor pulls out (skills, seniority, benefits,
    # etc.) that don't warrant their own columns yet.
    extracted_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Full raw scrape/LLM response, kept for reprocessing if extraction
    # logic improves later without re-fetching the source URL.
    raw_source: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    posted_at: Mapped[date | None] = mapped_column(Date)
    # Null until the first scan completes — the row itself is created
    # up front (see get_or_create_job_posting) so job_posting_id exists
    # immediately, before there's anything to scan.
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraction_status: Mapped[str] = mapped_column(
        String(20), default=ScanStatus.PENDING, nullable=False
    )

    url: Mapped["JobPostingUrl"] = relationship(back_populates="postings")
    # passive_deletes: job_posting_id is NOT NULL, so without this the ORM's
    # default "null out the child FK" behavior on parent delete violates
    # that constraint — verified live via DELETE /admin/listings/{id} on a
    # posting with a saved/applied UserJobApplication. The FK already
    # declares ondelete="CASCADE" (see UserJobApplication.job_posting_id),
    # so this just defers to the DB to remove the application row instead
    # of having the ORM fight the constraint.
    applications: Mapped[list["UserJobApplication"]] = relationship(
        back_populates="job_posting", passive_deletes=True
    )

    @property
    def apply_url(self) -> str:
        """The original posting URL to apply at — flattened here (instead of
        making every JobPostingRead consumer also load JobPostingUrl) since
        Pydantic's from_attributes reads plain properties the same as
        columns."""
        return self.url.url

    def __repr__(self) -> str:
        return f"<JobPosting title={self.title!r} company={self.company_name!r}>"
