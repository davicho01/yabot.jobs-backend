import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, get_db
from app.core.rate_limit import RateLimitExceeded
from app.models.enums import ScanStatus, WorkplaceType
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.models.user import User
from app.schemas.job import JobDetailRead, JobListRead, JobUrlSubmit, MetroRead
from app.services import geo
from app.services.job_locations import location_suggestions, metro_suggestions
from app.services.jobs import (
    build_job_search_statement,
    ensure_user_applicant,
    get_or_create_job_posting,
    rescan_job_url,
    to_job_detail,
)

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
    try:
        posting, url_row = get_or_create_job_posting(db, str(payload.url), current_user.id)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
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
    radius: int = Query(geo.RADIUS_MILES, ge=5, le=100),
    company: str | None = None,
    posted_within_days: int | None = Query(None, ge=1),
    workplace_type: WorkplaceType | None = None,
    salary_min: int | None = Query(None, ge=0),
    salary_max: int | None = Query(None, ge=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JobListRead:
    # Only postings that have been scanned, canonical ones only, plus whatever
    # filters were given — see build_job_search_statement, shared with the
    # saved-search alert sweep so matching is defined in exactly one place.
    stmt, order, search_area = build_job_search_statement(
        q=q,
        location=location,
        metro=metro,
        radius=radius,
        company=company,
        posted_within_days=posted_within_days,
        workplace_type=workplace_type,
        salary_min=salary_min,
        salary_max=salary_max,
    )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    # Batches both loads into one extra query each for the whole page, instead
    # of to_job_detail's url_row.postings access and also_posted_count's
    # posting.duplicates access lazy-loading per row (an N+1 each otherwise).
    stmt = (
        stmt.order_by(*order)
        .limit(page_size)
        .offset((page - 1) * page_size)
        .options(selectinload(JobPostingUrl.postings).selectinload(JobPosting.duplicates))
    )
    url_rows = db.scalars(stmt).all()
    return JobListRead(
        items=[to_job_detail(row) for row in url_rows],
        total=total,
        page=page,
        page_size=page_size,
        search_area=search_area,
    )


# Registered before /{url_id} so "locations" isn't swallowed by that route's
# uuid.UUID path param (which would 422 on non-UUID path segments).
@router.get("/locations", response_model=list[str])
def list_job_locations(
    q: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[str]:
    """Individual locations across all postings (a posting listing several
    contributes each one), most-used first. With `q`, only ones containing it,
    those starting with it ranked first. These are the raw scraped strings,
    facility names and all — the search box suggests GET /jobs/places instead."""
    return location_suggestions(db, q, limit)


@router.get("/places", response_model=list[str])
def list_job_places(q: str | None = None, limit: int = Query(10, ge=1, le=50)) -> list[str]:
    """Places to suggest as the location search is typed: "City, State, United
    States", "City, State (metro area)", "State, United States" or "United
    States". Searching one of them (GET /jobs?location=<label>) covers the city
    and the miles around it, the whole metro area, or the whole state."""
    return geo.place_suggestions(q, limit)


# Two path segments, unlike /{url_id}'s one, so — unlike /locations above —
# there's no ordering hazard here; kept next to the other /places routes
# purely for readability.
@router.get("/places/nearest", response_model=str | None)
def nearest_job_place(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180)) -> str | None:
    """The location search to default someone to from a browser geolocation
    fix — their nearest metro area when they're in (or near) one, so a small
    town doesn't default to a radius search too narrow to turn up much;
    otherwise their nearest known city. Same label(s) GET /jobs/places would
    offer, so the frontend can treat this like a picked suggestion. None if
    nothing knowable is close enough (see geo.nearest_default_location_label)."""
    return geo.nearest_default_location_label(lat, lon)


@router.get("/metros", response_model=list[MetroRead])
def list_job_metros(
    q: str | None = None,
    slug: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[MetroRead]:
    """Areas that have postings — Census metro/micro areas and states — most
    postings first. With `q`, the ones matching it (name prefix first). With
    `slug`, just that area — lets a page showing `?metro=<slug>` label it. A
    state's count includes every posting in it (metro areas included)."""
    return [
        MetroRead(slug=metro.slug, name=metro.name, kind=metro.kind, count=count)
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
