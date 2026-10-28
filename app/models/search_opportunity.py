from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Date, Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SearchOpportunity(Base):
    """What people searched on Google when yabot.jobs showed up, grouped by
    what they were after — a role (with a place, or remote), a company, a
    place — over the last search_console.TOTALS_DAYS, from Search Console
    (app.services.search_opportunities). Replaced as a whole each night."""

    __tablename__ = "search_opportunities"

    # "role=software-engineer;location=remote" (a collection key),
    # "company:nike", "location:houston-tx", or "query:<the search>"
    key: Mapped[str] = mapped_column(String(500), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # role | company | location | other
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    # For a role: the collection that would answer it (job_collections.collection_key)
    collection_key: Mapped[str | None] = mapped_column(String(300))
    open_jobs: Mapped[int | None] = mapped_column(Integer)  # None for "other"
    clicks: Mapped[int] = mapped_column(Integer, nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[float] = mapped_column(Float, nullable=False)  # average, 1 = top
    # The searches behind it and the pages Google showed, most shown first:
    # [{"query", "clicks", "impressions"}], [{"page", "impressions"}]
    queries: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    pages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    updated_on: Mapped[date] = mapped_column(Date, nullable=False)  # Eastern date of the import
