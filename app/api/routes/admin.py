import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_admin_user, get_db
from app.models.company import Company
from app.models.crawl_source import CrawlSource
from app.models.enums import FeedbackKind, FeedbackStatus
from app.models.feedback import Feedback
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.schemas.admin import (
    AdminCompanyDomainUpdate,
    AdminCompanyLogoUrl,
    AdminCompanyRead,
    AdminDashboardRead,
    ScanDayCount,
    ScanHourCount,
    ScanMonthCount,
    ScanWeekCount,
)
from app.schemas.feedback import AdminFeedbackListRead, AdminFeedbackRead, FeedbackStatusUpdate
from app.schemas.job import JobDetailRead, JobListRead
from app.services import admin as admin_service
from app.services import logo_images
from app.services.company_logos import (
    ORIGIN_UPLOAD,
    ORIGIN_URL,
    clear_manual_logo,
    get_or_create_company,
    logo_url_for,
    set_manual_domain,
    set_manual_logo,
    usable_domain_clause,
)
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


@router.get("/companies", response_model=list[AdminCompanyRead])
def list_companies(
    missing_domain: bool = Query(False),
    q: str | None = Query(None, max_length=255),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[AdminCompanyRead]:
    """Companies by how many canonical postings they have, most first —
    with missing_domain, the ones still showing a letter avatar instead of a
    logo (see app.services.company_logos)."""
    posting_count = (
        select(func.count())
        .where(JobPosting.company_key == Company.company_key, JobPosting.primary_posting_id.is_(None))
        .correlate(Company)
        .scalar_subquery()
    )
    stmt = select(Company, posting_count.label("posting_count")).order_by(desc("posting_count")).limit(limit)
    if missing_domain:
        stmt = stmt.where(~usable_domain_clause())
    if q:
        stmt = stmt.where(Company.display_name.ilike(f"%{q}%"))
    return [_to_admin_company(company, count) for company, count in db.execute(stmt)]


@router.patch("/companies", response_model=AdminCompanyRead)
def update_company_domain(payload: AdminCompanyDomainUpdate, db: Session = Depends(get_db)) -> AdminCompanyRead:
    company = db.scalar(select(Company).where(Company.company_key == payload.company_key))
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found.")
    try:
        set_manual_domain(db, company, (payload.domain or "").strip() or None)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    db.flush()
    return _to_admin_company(company)


@router.put("/companies/logo", response_model=AdminCompanyRead)
def set_company_logo_from_url(payload: AdminCompanyLogoUrl, db: Session = Depends(get_db)) -> AdminCompanyRead:
    """Copy the logo at a URL (an image, or a page it's on) as this
    company's logo — our own stored copy, pinned against the automatic sync."""
    try:
        result = logo_images.logo_from_url(payload.url)
    except logo_images.LogoRejected as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    company = get_or_create_company(db, payload.company_key, payload.display_name or payload.company_key)
    set_manual_logo(db, company, result.png, origin=ORIGIN_URL, source_url=result.source_url)
    return _to_admin_company(company)


@router.post("/companies/logo-upload", response_model=AdminCompanyRead)
async def upload_company_logo(
    company_key: str = Form(...),
    display_name: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> AdminCompanyRead:
    # Read at most one byte past the limit, so an oversize file is rejected
    # without being read whole.
    data = await file.read(logo_images.MAX_UPLOAD_BYTES + 1)
    try:
        png = logo_images.logo_from_upload(data)
    except logo_images.LogoRejected as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    company = get_or_create_company(db, company_key, display_name or company_key)
    set_manual_logo(db, company, png, origin=ORIGIN_UPLOAD, source_url=file.filename)
    return _to_admin_company(company)


@router.delete("/companies/logo", response_model=AdminCompanyRead)
def clear_company_logo(company_key: str = Query(...), db: Session = Depends(get_db)) -> AdminCompanyRead:
    """Back to the automatic logo, fetched on the next sync."""
    company = db.scalar(select(Company).where(Company.company_key == company_key))
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found.")
    clear_manual_logo(db, company)
    return _to_admin_company(company)


def _to_admin_company(company: Company, posting_count: int = 0) -> AdminCompanyRead:
    read = AdminCompanyRead.model_validate(company)
    read.logo_url = logo_url_for(company.logo_key)
    read.posting_count = posting_count or 0
    return read
