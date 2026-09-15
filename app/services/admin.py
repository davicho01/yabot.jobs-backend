from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.models.crawl_source import CrawlSource
from app.models.job_url import JobPostingUrl
from app.models.user import User

# (attribute name on the response, how far back the window goes)
_WINDOWS: list[tuple[str, timedelta]] = [
    ("last_24h", timedelta(hours=24)),
    ("last_7d", timedelta(days=7)),
    ("last_30d", timedelta(days=30)),
    ("last_90d", timedelta(days=90)),
]


def _window_counts(
    db: Session,
    model: type[Any],
    timestamp_column: ColumnElement[datetime | None],
    extra_filter: ColumnElement[bool] | None = None,
) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    counts: dict[str, int] = {}
    for key, span in _WINDOWS:
        stmt = select(func.count()).select_from(model).where(timestamp_column >= now - span)
        if extra_filter is not None:
            stmt = stmt.where(extra_filter)
        counts[key] = db.scalar(stmt) or 0
    return counts


def get_dashboard_stats(db: Session) -> dict:
    return {
        "totals": {
            "job_listings": db.scalar(select(func.count()).select_from(JobPostingUrl)) or 0,
            "crawl_sources": db.scalar(select(func.count()).select_from(CrawlSource)) or 0,
            "users": db.scalar(select(func.count()).select_from(User)) or 0,
        },
        "users_joined": _window_counts(db, User, User.created_at),
        "user_activity": _window_counts(db, User, User.last_login_at),
        "application_scans": _window_counts(db, JobPostingUrl, JobPostingUrl.last_scanned_at),
    }


def get_crawl_source_stats(db: Session, source: CrawlSource) -> dict:
    scoped = JobPostingUrl.crawl_source_id == source.id
    return {
        "source": source,
        "total_listings": db.scalar(select(func.count()).select_from(JobPostingUrl).where(scoped)) or 0,
        "listings_added": _window_counts(db, JobPostingUrl, JobPostingUrl.created_at, extra_filter=scoped),
        "scans": _window_counts(db, JobPostingUrl, JobPostingUrl.last_scanned_at, extra_filter=scoped),
    }
