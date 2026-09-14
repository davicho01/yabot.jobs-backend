import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.enums import ScanStatus, WorkplaceType
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.models.user import User
from app.schemas.job import JobDetailRead, JobListRead, JobUrlSubmit
from app.services.jobs import ensure_user_applicant, get_or_create_job_posting, rescan_job_url

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _to_detail(url_row: JobPostingUrl) -> JobDetailRead:
    latest_posting = url_row.postings[0] if url_row.postings else None
    return JobDetailRead(url=url_row, posting=latest_posting)


@router.post(
    "",
    response_model=JobDetailRead,
    status_code=status.HTTP_201_CREATED,
    responses={202: {"model": JobDetailRead, "description": "Scan queued; poll GET /jobs/{url_id}."}},
)
def submit_job_url(
    payload: JobUrlSubmit,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobDetailRead:
    posting, url_row = get_or_create_job_posting(db, str(payload.url), current_user.id)
    ensure_user_applicant(db, current_user.id, posting.id)
    if posting.extraction_status == ScanStatus.PENDING:
        # Scan queued (see app.services.job_queue) but not finished yet —
        # poll GET /jobs/{url_id} for the result.
        response.status_code = status.HTTP_202_ACCEPTED
    db.flush()
    db.refresh(url_row)
    return _to_detail(url_row)


@router.get("", response_model=JobListRead)
def list_job_urls(
    q: str | None = None,
    location: str | None = None,
    remote_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JobListRead:
    stmt = select(JobPostingUrl)
    if q or location or remote_only:
        stmt = stmt.join(JobPosting, JobPosting.url_id == JobPostingUrl.id).distinct()
        if q:
            stmt = stmt.where(JobPosting.title.ilike(f"%{q}%"))
        if location:
            stmt = stmt.where(JobPosting.location.ilike(f"%{location}%"))
        if remote_only:
            stmt = stmt.where(JobPosting.workplace_type == WorkplaceType.REMOTE)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    stmt = stmt.order_by(JobPostingUrl.created_at.desc()).limit(page_size).offset((page - 1) * page_size)
    url_rows = db.scalars(stmt).all()
    return JobListRead(items=[_to_detail(row) for row in url_rows], total=total, page=page, page_size=page_size)


# Registered before /{url_id} so "locations" isn't swallowed by that route's
# uuid.UUID path param (which would 422 on non-UUID path segments).
@router.get("/locations", response_model=list[str])
def list_job_locations(db: Session = Depends(get_db)) -> list[str]:
    stmt = (
        select(JobPosting.location)
        .where(JobPosting.location.is_not(None))
        .distinct()
        .order_by(JobPosting.location)
        .limit(200)
    )
    return list(db.scalars(stmt).all())


@router.get("/{url_id}", response_model=JobDetailRead)
def get_job_url(url_id: uuid.UUID, db: Session = Depends(get_db)) -> JobDetailRead:
    url_row = db.get(JobPostingUrl, url_id)
    if url_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job URL not found.")
    return _to_detail(url_row)


@router.post("/{url_id}/rescan", response_model=JobDetailRead)
def rescan_job(
    url_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobDetailRead:
    url_row = db.get(JobPostingUrl, url_id)
    if url_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job URL not found.")
    rescan_job_url(db, url_row)
    db.flush()
    db.refresh(url_row)
    return _to_detail(url_row)
