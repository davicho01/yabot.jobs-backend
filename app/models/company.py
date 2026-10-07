from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Company(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A company's own website domain and its stored logo (see
    app.services.company_logos).

    Keyed by JobPosting.company_key, the normalized company name dedup
    already computes. That's a plain join, not a foreign key, so postings
    and dedup don't depend on this table at all, and a company row can
    exist (or not) independently of any one posting.

    domain_source records where `domain` came from, which decides what may
    overwrite it: "manual" (an admin set it) is never touched automatically;
    "jsonld" (the posting's own hiringOrganization url/sameAs) beats
    "site_host" (the careers site's host, when that's the company's own
    site rather than an ATS platform's). Null domain means not resolved yet.
    """

    __tablename__ = "companies"

    company_key: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    # The most recently seen display form of the name, for admin listings.
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    domain_source: Mapped[str | None] = mapped_column(String(20))

    # The company's logo, stored as our own copy (see
    # app.services.company_logos): logo_key is its path in the static-pages
    # store, served at {seo_pages_base_url}/{logo_key}. logo_origin says
    # where it came from — "logo_dev" (automatic, by logo_domain) or "url" /
    # "upload" (set by an admin, which the automatic sync never overwrites).
    # logo_etag is logo.dev's, so an unchanged logo isn't re-downloaded.
    # logo_status is the last automatic attempt's outcome (ok / none /
    # error), null if never tried.
    logo_key: Mapped[str | None] = mapped_column(String(300))
    logo_domain: Mapped[str | None] = mapped_column(String(255))
    logo_source_url: Mapped[str | None] = mapped_column(Text)
    logo_status: Mapped[str | None] = mapped_column(String(20))
    logo_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    logo_origin: Mapped[str | None] = mapped_column(String(20))
    logo_etag: Mapped[str | None] = mapped_column(String(200))

    # The last look for this company's own careers site (see
    # app.services.official_sites), for a company whose jobs we only found
    # somewhere else: when, and what it found ("found" / "none" / "error").
    official_site_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    official_site_result: Mapped[str | None] = mapped_column(String(20))

    def __repr__(self) -> str:
        return f"<Company key={self.company_key!r} domain={self.domain!r}>"
