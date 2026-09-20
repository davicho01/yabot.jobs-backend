import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import false, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.enums import ScanStatus, WorkplaceType
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.models.user import User
from app.schemas.job import JobDetailRead, JobListRead, JobUrlSubmit, MetroRead
from app.services import geo
from app.services.job_locations import location_matches, location_suggestions, metro_suggestions
from app.services.jobs import ensure_user_applicant, get_or_create_job_posting, rescan_job_url, to_job_detail

router = APIRouter(prefix="/jobs", tags=["jobs"])


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
    return to_job_detail(url_row)


@router.get("", response_model=JobListRead)
def list_job_urls(
    q: str | None = None,
    location: str | None = None,
    metro: str | None = None,
    company: str | None = None,
    posted_within_days: int | None = Query(None, ge=1),
    workplace_type: WorkplaceType | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JobListRead:
    stmt = select(JobPostingUrl)
    if q or location or metro or company or posted_within_days or workplace_type:
        stmt = stmt.join(JobPosting, JobPosting.url_id == JobPostingUrl.id).distinct()
        if q:
            stmt = stmt.where(JobPosting.title.ilike(f"%{q}%"))
        if location:
            stmt = stmt.where(location_matches(f"%{location}%"))
        if metro:
            metro_area = geo.metro_by_slug(metro)
            # An unknown slug matches nothing (rather than erroring): a stale
            # shared link should just show an empty board.
            stmt = stmt.where(JobPosting.metros.contains([metro_area.code]) if metro_area else false())
        if company:
            stmt = stmt.where(JobPosting.company_name.ilike(f"%{company}%"))
        if posted_within_days:
            stmt = stmt.where(JobPosting.posted_at >= date.today() - timedelta(days=posted_within_days))
        if workplace_type:
            stmt = stmt.where(JobPosting.workplace_type == workplace_type)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    stmt = stmt.order_by(JobPostingUrl.created_at.desc()).limit(page_size).offset((page - 1) * page_size)
    url_rows = db.scalars(stmt).all()
    return JobListRead(items=[to_job_detail(row) for row in url_rows], total=total, page=page, page_size=page_size)


# Registered before /{url_id} so "locations" isn't swallowed by that route's
# uuid.UUID path param (which would 422 on non-UUID path segments).
@router.get("/locations", response_model=list[str])
def list_job_locations(
    q: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    unresolved_only: bool = False,
    db: Session = Depends(get_db),
) -> list[str]:
    """Individual locations across all postings (a posting listing several
    contributes each one), most-used first. With `q`, only ones containing it,
    those starting with it ranked first — meant to back a typeahead. With
    `unresolved_only`, leaves out places that belong to a metro area (those are
    suggested by GET /jobs/metros instead)."""
    return location_suggestions(db, q, limit, unresolved_only=unresolved_only)


@router.get("/metros", response_model=list[MetroRead])
def list_job_metros(
    q: str | None = None,
    slug: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[MetroRead]:
    """Census metro/micro areas that have postings, most postings first. With
    `q`, the ones matching it (name prefix first). With `slug`, just that area —
    lets a page showing `?metro=<slug>` label it."""
    return [
        MetroRead(slug=metro.slug, name=metro.name, count=count)
        for metro, count in metro_suggestions(db, q, limit, slug=slug)
    ]


@router.get("/{url_id}", response_model=JobDetailRead)
def get_job_url(url_id: uuid.UUID, db: Session = Depends(get_db)) -> JobDetailRead:
    url_row = db.get(JobPostingUrl, url_id)
    if url_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job URL not found.")
    return to_job_detail(url_row)


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
    return to_job_detail(url_row)
