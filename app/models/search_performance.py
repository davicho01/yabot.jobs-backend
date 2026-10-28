from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SearchPerformanceDay(Base):
    """One page's Google search results for one day, from Search Console
    (app.services.search_console): how often it was shown, clicked, and its
    average position. Pages Google didn't show that day have no row.
    Search Console keeps 16 months; this keeps what was fetched."""

    __tablename__ = "search_performance_daily"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    # The page's path without the leading slash, as the static generator
    # writes it ("jobs/us/software-engineer"); "" for the home page.
    page: Mapped[str] = mapped_column(String(500), primary_key=True)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[float] = mapped_column(Float, nullable=False)  # average, 1 = top
