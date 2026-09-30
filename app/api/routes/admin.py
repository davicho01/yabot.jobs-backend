import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_admin_user, get_db
from app.models.crawl_source import CrawlSource
from app.models.enums import FeedbackKind, FeedbackStatus
from app.models.feedback import Feedback
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.schemas.admin import AdminDashboardRead, ScanDayCount, ScanHourCount, ScanMonthCount, ScanWeekCount
from app.schemas.feedback import AdminFeedbackListRead, AdminFeedbackRead, FeedbackStatusUpdate
from app.schemas.job import JobDetailRead, JobListRead
from app.services import admin as admin_service
from app.services.jobs import dismiss_job_flag, to_job_detail

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(get_current_admin_user)])


@router.get("/dashboard", response_model=AdminDashboardRead)
def get_dashboard(db: Session = Depends(get_db)) -> dict:
    return admin_service.get_dashboard_stats(db)


@router.get("/scans-by-day", response_model=list[ScanDayCount])
def get_scans_by_day(days: int = Query(90, ge=1, le=365), db: Session = Depends(get_db)) -> list[dict]:
    return admin_service.get_scans_by_day(db, days=days)


@router.get("/scans-by-hour", response_model=list[ScanHourCount])
def get_scans_by_hour(
    hours: int = Query(24, ge=1, le=168),
    end: datetime | None = Query(None),
    db: Session = Depends(get_db),
) -> list[dict]:
    return admin_service.get_scans_by_hour(db, hours=hours, end=end)


@router.get("/scans-by-week", response_model=list[ScanWeekCount])
def get_scans_by_week(weeks: int = Query(26, ge=1, le=104), db: Session = Depends(get_db)) -> list[dict]:
    return admin_service.get_scans_by_week(db, weeks=weeks)


@router.get("/scans-by-month", response_model=list[ScanMonthCount])
def get_scans_by_month(months: int = Query(6, ge=1, le=60), db: Session = Depends(get_db)) -> list[dict]:
    return admin_service.get_scans_by_month(db, months=months)


JOB_SORT_COLUMNS = {
    "company": JobPosting.company_name,
    "title": JobPosting.title,
    "status": JobPostingUrl.scan_status,
    "discovered": JobPostingUrl.created_at,
}


@router.get("/jobs", response_model=JobListRead)
def get_jobs(
    source_id: uuid.UUID | None = Query(None),
    scan_from: datetime | None = Query(None),
    scan_to: datetime | None = Query(None),
    flagged: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: Literal["company", "title", "status", "discovered"] = Query("discovered"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
    db: Session = Depends(get_db),
) -> JobListRead:
    """Lists job listings, optionally scoped to one crawl source and/or a
    last_scanned_at window — the drill-down target for a ScanActivityChart
    bar (source-scoped from the source stats page, unscoped from the
    dashboard's all-sources chart) as well as the plain per-source listing.
    `flagged=true` is the user-report triage queue instead (see POST
    /jobs/{url_id}/flag)."""
    if source_id is not None and db.get(CrawlSource, source_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")

    stmt = select(JobPostingUrl)
    if source_id is not None:
        stmt = stmt.where(JobPostingUrl.crawl_source_id == source_id)
    if scan_from is not None:
        stmt = stmt.where(JobPostingUrl.last_scanned_at >= scan_from)
    if scan_to is not None:
        stmt = stmt.where(JobPostingUrl.last_scanned_at <= scan_to)
    if flagged is not None:
        stmt = stmt.where(JobPostingUrl.flagged_at.isnot(None) if flagged else JobPostingUrl.flagged_at.is_(None))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    # JobPosting.url_id is unique, so this join can't fan out JobPostingUrl rows.
    if sort_by in ("company", "title"):
        stmt = stmt.outerjoin(JobPosting, JobPosting.url_id == JobPostingUrl.id)
    order_fn = asc if sort_order == "asc" else desc
    stmt = stmt.order_by(order_fn(JOB_SORT_COLUMNS[sort_by]), JobPostingUrl.created_at.desc())
    stmt = stmt.limit(page_size).offset((page - 1) * page_size)
    url_rows = db.scalars(stmt).all()
    return JobListRead(
        items=[to_job_detail(row, include_flag=True) for row in url_rows], total=total, page=page, page_size=page_size
    )


@router.delete("/listings/{url_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_listing(url_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    url_row = db.get(JobPostingUrl, url_id)
    if url_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found.")
    db.delete(url_row)


@router.post("/listings/{url_id}/flag/dismiss", response_model=JobDetailRead)
def dismiss_listing_flag(url_id: uuid.UUID, db: Session = Depends(get_db)) -> JobDetailRead:
    """Clear a listing's open flag report once it's been looked into
    (typically after rescanning it or fixing the adapter)."""
    url_row = db.get(JobPostingUrl, url_id)
    if url_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found.")
    dismiss_job_flag(db, url_row)
    db.flush()
    db.refresh(url_row)
    return to_job_detail(url_row, include_flag=True)


def _to_admin_feedback(feedback: Feedback) -> AdminFeedbackRead:
    return AdminFeedbackRead(
        id=feedback.id,
        kind=feedback.kind,
        message=feedback.message,
        rating=feedback.rating,
        page_url=feedback.page_url,
        status=feedback.status,
        created_at=feedback.created_at,
        user_id=feedback.user_id,
        user_email=feedback.user.email,
        user_agent=feedback.user_agent,
    )


@router.get("/feedback", response_model=AdminFeedbackListRead)
def list_feedback(
    status_filter: FeedbackStatus | None = Query(None, alias="status"),
    kind: FeedbackKind | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> AdminFeedbackListRead:
    """The triage queue for POST /feedback submissions, newest first.
    `new_count` ignores the filters so the page can always show how many
    are still waiting."""
    stmt = select(Feedback)
    if status_filter is not None:
        stmt = stmt.where(Feedback.status == status_filter)
    if kind is not None:
        stmt = stmt.where(Feedback.kind == kind)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    new_count = db.scalar(select(func.count()).select_from(Feedback).where(Feedback.status == FeedbackStatus.NEW)) or 0
    rows = db.scalars(
        stmt.options(joinedload(Feedback.user))
        .order_by(Feedback.created_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    return AdminFeedbackListRead(items=[_to_admin_feedback(row) for row in rows], total=total, new_count=new_count)


@router.patch("/feedback/{feedback_id}", response_model=AdminFeedbackRead)
def update_feedback_status(
    feedback_id: uuid.UUID, payload: FeedbackStatusUpdate, db: Session = Depends(get_db)
) -> AdminFeedbackRead:
    feedback = db.get(Feedback, feedback_id)
    if feedback is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feedback not found.")
    feedback.status = payload.status
    db.flush()
    return _to_admin_feedback(feedback)
