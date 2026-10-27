import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, distinct, func, select
from sqlalchemy.orm import Session

from app.models.crawl_source import CrawlSource
from app.models.enums import ScanStatus, UserRole
from app.models.job_url import JobPostingUrl
from app.models.user import User
from app.models.user_activity import UserActiveHour

UTC = ZoneInfo("UTC")

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
            "jobs_flagged": db.scalar(
                select(func.count()).select_from(JobPostingUrl).where(JobPostingUrl.flagged_at.isnot(None))
            )
            or 0,
            "crawl_sources": db.scalar(select(func.count()).select_from(CrawlSource)) or 0,
            "crawl_sources_flagged": db.scalar(
                select(func.count()).select_from(CrawlSource).where(CrawlSource.coverage_flagged_at.isnot(None))
            )
            or 0,
            "users": db.scalar(select(func.count()).select_from(User)) or 0,
        },
        "users_joined": _window_counts(db, User, User.created_at),
        "user_activity": _window_counts(db, User, User.last_login_at),
        "application_scans": _window_counts(db, JobPostingUrl, JobPostingUrl.last_scanned_at),
        "scan_status_counts": _scan_status_counts(db),
    }


def _local_trunc(field: str, column: ColumnElement[datetime | None], tz: ZoneInfo) -> ColumnElement[datetime]:
    """`column` truncated to `field` (day/week/month) on `tz`'s wall clock,
    so a 9pm-Mountain scan lands on that local date rather than the next UTC
    one. Postgres timezone() turns the timestamptz into local time first."""
    return func.date_trunc(field, func.timezone(tz.key, column))


def _local_midnight(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time.min, tzinfo=tz)


def _counts_by_day(
    db: Session,
    column: ColumnElement[datetime | None],
    days: int,
    extra_filter: ColumnElement[bool] | None = None,
    count: ColumnElement[int] | None = None,
    tz: ZoneInfo = UTC,
) -> list[dict]:
    """Daily counts of rows (or of `count`, e.g. distinct users) whose
    `column` falls in the last `days` calendar days in `tz` (today
    inclusive), with zero-filled gaps so the chart has one point per day
    regardless of activity."""
    start_date = datetime.now(tz).date() - timedelta(days=days - 1)

    stmt = (
        select(_local_trunc("day", column, tz).label("day"), (count if count is not None else func.count()).label("count"))
        .where(column >= _local_midnight(start_date, tz))
        .group_by("day")
    )
    if extra_filter is not None:
        stmt = stmt.where(extra_filter)

    counts_by_day = {row.day.date(): row.count for row in db.execute(stmt).all()}

    return [
        {"date": day, "count": counts_by_day.get(day, 0)}
        for day in (start_date + timedelta(days=i) for i in range(days))
    ]


def _counts_by_hour(
    db: Session,
    column: ColumnElement[datetime | None],
    hours: int,
    end: datetime | None = None,
    extra_filter: ColumnElement[bool] | None = None,
    count: ColumnElement[int] | None = None,
) -> list[dict]:
    """Hourly counts for a `hours`-hour window ending at `end`'s hour
    (current hour inclusive by default), with zero-filled gaps so the chart
    has one point per hour regardless of activity. `end` lets a caller page
    the window into the past — it's clamped so the window can never reach
    past the real current hour."""
    now = datetime.now(timezone.utc)
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    if end is not None:
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        else:
            end = end.astimezone(timezone.utc)
        end_hour = min(end.replace(minute=0, second=0, microsecond=0), current_hour)
    else:
        end_hour = current_hour
    start = end_hour - timedelta(hours=hours - 1)

    stmt = (
        select(func.date_trunc("hour", column).label("hour"), (count if count is not None else func.count()).label("count"))
        .where(column >= start)
        .where(column < end_hour + timedelta(hours=1))
        .group_by("hour")
    )
    if extra_filter is not None:
        stmt = stmt.where(extra_filter)

    counts_by_hour = {row.hour.replace(tzinfo=timezone.utc): row.count for row in db.execute(stmt).all()}

    return [
        {"hour": hour, "count": counts_by_hour.get(hour, 0)}
        for hour in (start + timedelta(hours=i) for i in range(hours))
    ]


def _counts_by_week(
    db: Session,
    column: ColumnElement[datetime | None],
    weeks: int,
    extra_filter: ColumnElement[bool] | None = None,
    count: ColumnElement[int] | None = None,
    tz: ZoneInfo = UTC,
) -> list[dict]:
    """Weekly (Monday-start, matching the frontend's ISO-week bucketing)
    counts for the last `weeks` weeks in `tz` (current week inclusive), with
    zero-filled gaps so the chart has one point per week regardless of
    activity."""
    today = datetime.now(tz).date()
    start_date = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)

    stmt = (
        select(_local_trunc("week", column, tz).label("week"), (count if count is not None else func.count()).label("count"))
        .where(column >= _local_midnight(start_date, tz))
        .group_by("week")
    )
    if extra_filter is not None:
        stmt = stmt.where(extra_filter)

    counts_by_week = {row.week.date(): row.count for row in db.execute(stmt).all()}

    return [
        {"week": week, "count": counts_by_week.get(week, 0)}
        for week in (start_date + timedelta(weeks=i) for i in range(weeks))
    ]


def _add_months(d: date, months: int) -> date:
    total = d.month - 1 + months
    return date(d.year + total // 12, total % 12 + 1, 1)


def _counts_by_month(
    db: Session,
    column: ColumnElement[datetime | None],
    months: int,
    extra_filter: ColumnElement[bool] | None = None,
    count: ColumnElement[int] | None = None,
    tz: ZoneInfo = UTC,
) -> list[dict]:
    """Calendar-month counts for the last `months` months in `tz` (current
    month inclusive), with zero-filled gaps so the chart has one point per
    month regardless of activity."""
    start = _add_months(datetime.now(tz).date().replace(day=1), -(months - 1))

    stmt = (
        select(_local_trunc("month", column, tz).label("month"), (count if count is not None else func.count()).label("count"))
        .where(column >= _local_midnight(start, tz))
        .group_by("month")
    )
    if extra_filter is not None:
        stmt = stmt.where(extra_filter)

    counts_by_month = {row.month.date(): row.count for row in db.execute(stmt).all()}

    return [
        {"month": month, "count": counts_by_month.get(month, 0)}
        for month in (_add_months(start, i) for i in range(months))
    ]


def _source_filter(crawl_source_id: uuid.UUID | None) -> ColumnElement[bool] | None:
    return JobPostingUrl.crawl_source_id == crawl_source_id if crawl_source_id is not None else None


def get_scans_by_day(
    db: Session, days: int, crawl_source_id: uuid.UUID | None = None, tz: ZoneInfo = UTC
) -> list[dict]:
    return _counts_by_day(db, JobPostingUrl.last_scanned_at, days, _source_filter(crawl_source_id), tz=tz)


def get_scans_by_hour(
    db: Session, hours: int, crawl_source_id: uuid.UUID | None = None, end: datetime | None = None
) -> list[dict]:
    return _counts_by_hour(db, JobPostingUrl.last_scanned_at, hours, end, _source_filter(crawl_source_id))


def get_scans_by_week(
    db: Session, weeks: int, crawl_source_id: uuid.UUID | None = None, tz: ZoneInfo = UTC
) -> list[dict]:
    return _counts_by_week(db, JobPostingUrl.last_scanned_at, weeks, _source_filter(crawl_source_id), tz=tz)


def get_scans_by_month(
    db: Session, months: int, crawl_source_id: uuid.UUID | None = None, tz: ZoneInfo = UTC
) -> list[dict]:
    return _counts_by_month(db, JobPostingUrl.last_scanned_at, months, _source_filter(crawl_source_id), tz=tz)


def get_users_joined_by_day(db: Session, days: int, tz: ZoneInfo = UTC) -> list[dict]:
    return _counts_by_day(db, User.created_at, days, tz=tz)


def get_users_joined_by_hour(db: Session, hours: int, end: datetime | None = None) -> list[dict]:
    return _counts_by_hour(db, User.created_at, hours, end)


def get_users_joined_by_week(db: Session, weeks: int, tz: ZoneInfo = UTC) -> list[dict]:
    return _counts_by_week(db, User.created_at, weeks, tz=tz)


def get_users_joined_by_month(db: Session, months: int, tz: ZoneInfo = UTC) -> list[dict]:
    return _counts_by_month(db, User.created_at, months, tz=tz)


def _returning_filter(tz: ZoneInfo) -> ColumnElement[bool]:
    """A returning user in an hour/day/week/month: a non-admin who used the
    app then and signed up before that local day (in `tz`) started."""
    local_day_start = func.timezone(tz.key, _local_trunc("day", UserActiveHour.hour, tz))
    return and_(
        UserActiveHour.user_id == User.id,
        User.role != UserRole.ADMIN,
        User.created_at < local_day_start,
    )


# Distinct users, so a day/week/month counts each person once however many
# hours they were on.
_DISTINCT_USERS = func.count(distinct(UserActiveHour.user_id))


def get_returning_users_by_hour(
    db: Session, hours: int, end: datetime | None = None, tz: ZoneInfo = UTC
) -> list[dict]:
    return _counts_by_hour(db, UserActiveHour.hour, hours, end, _returning_filter(tz), _DISTINCT_USERS)


def get_returning_users_by_day(db: Session, days: int, tz: ZoneInfo = UTC) -> list[dict]:
    return _counts_by_day(db, UserActiveHour.hour, days, _returning_filter(tz), _DISTINCT_USERS, tz)


def get_returning_users_by_week(db: Session, weeks: int, tz: ZoneInfo = UTC) -> list[dict]:
    return _counts_by_week(db, UserActiveHour.hour, weeks, _returning_filter(tz), _DISTINCT_USERS, tz)


def get_returning_users_by_month(db: Session, months: int, tz: ZoneInfo = UTC) -> list[dict]:
    return _counts_by_month(db, UserActiveHour.hour, months, _returning_filter(tz), _DISTINCT_USERS, tz)


def get_crawl_source_stats(db: Session, source: CrawlSource) -> dict:
    scoped = JobPostingUrl.crawl_source_id == source.id
    return {
        "source": source,
        "total_listings": db.scalar(select(func.count()).select_from(JobPostingUrl).where(scoped)) or 0,
        "listings_added": _window_counts(db, JobPostingUrl, JobPostingUrl.created_at, extra_filter=scoped),
        "scans": _window_counts(db, JobPostingUrl, JobPostingUrl.last_scanned_at, extra_filter=scoped),
        "scan_status_counts": _scan_status_counts(db, extra_filter=scoped),
    }
