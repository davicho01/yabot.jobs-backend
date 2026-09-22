import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.models.crawl_source import CrawlSource
from app.models.enums import ScanStatus
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


def _scan_status_counts(db: Session, extra_filter: ColumnElement[bool] | None = None) -> dict[str, int]:
    """How many JobPostingUrls are in each ScanStatus — zero-filled so a
    status with no rows (hopefully needs_review) still shows as 0 rather
    than being missing. Distinguishes "still retrying" (failed) from "gave
    up" (needs_review), a split app.models.crawl_source.CrawlSource.last_error
    alone never gave any visibility into."""
    stmt = select(JobPostingUrl.scan_status, func.count()).group_by(JobPostingUrl.scan_status)
    if extra_filter is not None:
        stmt = stmt.where(extra_filter)
    counts: dict[str, int] = {status.value: 0 for status in ScanStatus}
    counts.update({status: count for status, count in db.execute(stmt).all()})
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
        "scan_status_counts": _scan_status_counts(db),
    }


def get_scans_by_day(db: Session, days: int, crawl_source_id: uuid.UUID | None = None) -> list[dict]:
    """Daily scan counts for the last `days` days (today inclusive), with
    zero-filled gaps so the chart has one point per calendar day regardless
    of scan activity — the frontend buckets these into week/month views."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

    stmt = (
        select(
            func.date_trunc("day", JobPostingUrl.last_scanned_at).label("day"),
            func.count().label("count"),
        )
        .where(JobPostingUrl.last_scanned_at >= start)
        .group_by("day")
    )
    if crawl_source_id is not None:
        stmt = stmt.where(JobPostingUrl.crawl_source_id == crawl_source_id)

    counts_by_day = {row.day.date(): row.count for row in db.execute(stmt).all()}

    return [
        {"date": day, "count": counts_by_day.get(day, 0)}
        for day in (start.date() + timedelta(days=i) for i in range(days))
    ]


def get_scans_by_hour(db: Session, hours: int, crawl_source_id: uuid.UUID | None = None) -> list[dict]:
    """Hourly scan counts for the last `hours` hours (current hour inclusive),
    with zero-filled gaps so the chart has one point per hour regardless of
    scan activity."""
    now = datetime.now(timezone.utc)
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours - 1)

    stmt = (
        select(
            func.date_trunc("hour", JobPostingUrl.last_scanned_at).label("hour"),
            func.count().label("count"),
        )
        .where(JobPostingUrl.last_scanned_at >= start)
        .group_by("hour")
    )
    if crawl_source_id is not None:
        stmt = stmt.where(JobPostingUrl.crawl_source_id == crawl_source_id)

    counts_by_hour = {row.hour.replace(tzinfo=timezone.utc): row.count for row in db.execute(stmt).all()}

    return [
        {"hour": hour, "count": counts_by_hour.get(hour, 0)}
        for hour in (start + timedelta(hours=i) for i in range(hours))
    ]


def get_crawl_source_stats(db: Session, source: CrawlSource) -> dict:
    scoped = JobPostingUrl.crawl_source_id == source.id
    return {
        "source": source,
        "total_listings": db.scalar(select(func.count()).select_from(JobPostingUrl).where(scoped)) or 0,
        "listings_added": _window_counts(db, JobPostingUrl, JobPostingUrl.created_at, extra_filter=scoped),
        "scans": _window_counts(db, JobPostingUrl, JobPostingUrl.last_scanned_at, extra_filter=scoped),
        "scan_status_counts": _scan_status_counts(db, extra_filter=scoped),
    }
