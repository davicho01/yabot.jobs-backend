import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user, get_db
from app.models.crawl_source import CrawlSource
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.schemas.admin import AdminDashboardRead, ScanDayCount, ScanHourCount
from app.schemas.job import JobListRead
from app.services import admin as admin_service
from app.services.jobs import to_job_detail

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(get_current_admin_user)])


@router.get("/dashboard", response_model=AdminDashboardRead)
def get_dashboard(db: Session = Depends(get_db)) -> dict:
    return admin_service.get_dashboard_stats(db)


@router.get("/scans-by-day", response_model=list[ScanDayCount])
def get_scans_by_day(days: int = Query(90, ge=1, le=365), db: Session = Depends(get_db)) -> list[dict]:
    return admin_service.get_scans_by_day(db, days=days)


@router.get("/scans-by-hour", response_model=list[ScanHourCount])
def get_scans_by_hour(hours: int = Query(24, ge=1, le=168), db: Session = Depends(get_db)) -> list[dict]:
    return admin_service.get_scans_by_hour(db, hours=hours)


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
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: Literal["company", "title", "status", "discovered"] = Query("discovered"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
    db: Session = Depends(get_db),
) -> JobListRead:
    """Lists job listings, optionally scoped to one crawl source and/or a
    last_scanned_at window — the drill-down target for a ScanActivityChart
    bar (source-scoped from the source stats page, unscoped from the
    dashboard's all-sources chart) as well as the plain per-source listing."""
    if source_id is not None and db.get(CrawlSource, source_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")

    stmt = select(JobPostingUrl)
    if source_id is not None:
        stmt = stmt.where(JobPostingUrl.crawl_source_id == source_id)
    if scan_from is not None:
        stmt = stmt.where(JobPostingUrl.last_scanned_at >= scan_from)
    if scan_to is not None:
        stmt = stmt.where(JobPostingUrl.last_scanned_at <= scan_to)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    # JobPosting.url_id is unique, so this join can't fan out JobPostingUrl rows.
    if sort_by in ("company", "title"):
        stmt = stmt.outerjoin(JobPosting, JobPosting.url_id == JobPostingUrl.id)
    order_fn = asc if sort_order == "asc" else desc
    stmt = stmt.order_by(order_fn(JOB_SORT_COLUMNS[sort_by]), JobPostingUrl.created_at.desc())
    stmt = stmt.limit(page_size).offset((page - 1) * page_size)
    url_rows = db.scalars(stmt).all()
    return JobListRead(items=[to_job_detail(row) for row in url_rows], total=total, page=page, page_size=page_size)


@router.delete("/listings/{url_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_listing(url_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    url_row = db.get(JobPostingUrl, url_id)
    if url_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found.")
    db.delete(url_row)
