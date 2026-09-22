import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import false, func, or_, select, true
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, get_db
from app.core.rate_limit import RateLimitExceeded
from app.models.enums import ScanStatus, WorkplaceType
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.models.user import User
from app.schemas.job import JobDetailRead, JobListRead, JobUrlSubmit, MetroRead, SearchAreaRead
from app.services import geo
from app.services.job_locations import location_matches, location_suggestions, metro_suggestions, radius_search
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
    # Only postings that have been scanned: one still waiting on its scan (or that
    # failed) has no title to show, so it would just be an empty "Scanning posting…"
    # card, and — being newest first — a bulk crawl would fill whole pages with them.
    # primary_posting_id IS NULL: only canonical rows — a posting matched to
    # an existing one (see app.services.job_dedup) is the same job found at
    # another URL, hidden here (see JobPosting.also_posted_count) but still
    # independently reachable via GET /jobs/{url_id}.
    stmt = (
        select(JobPostingUrl)
        .join(JobPosting, JobPosting.url_id == JobPostingUrl.id)
        .where(
            JobPosting.extraction_status == ScanStatus.SUCCESS,
            JobPosting.title.is_not(None),
            JobPosting.primary_posting_id.is_(None),
        )
    )
    order = [JobPostingUrl.created_at.desc()]
    search_area: SearchAreaRead | None = None
    # salary_min/salary_max use `is not None`, not truthy-`or` like the rest of
    # these — 0 is a valid, meaningful value for both (Query(..., ge=0)) and a
    # plain `or` would silently treat salary_min=0 as "not provided".
    if (
        q
        or location
        or metro
        or company
        or posted_within_days
        or workplace_type
        or salary_min is not None
        or salary_max is not None
    ):
        stmt = stmt.distinct()
        if q:
            stmt = stmt.where(JobPosting.title.ilike(f"%{q}%"))
        if location:
            metro_area = geo.search_metro(location)
            place = None if metro_area else geo.search_place(location)
            if metro_area is not None:
                # "Salt Lake City, Utah (metro area)": everything filed under that area.
                stmt = stmt.where(JobPosting.metros.contains([metro_area.code]))
            elif place is not None:
                # A city: postings with a place within `radius` miles of it, the
                # city's own first, then nearer before farther (see radius_search).
                nearest, near, band = radius_search(place, radius)
                stmt = stmt.join(nearest, true()).where(near).add_columns(band.label("distance_band"))
                order = [band, JobPostingUrl.created_at.desc()]
                search_area = SearchAreaRead(label=geo.place_label(place), radius_miles=radius)
            else:
                # A state or "United States" also finds postings filed under it
                # however they spelled the place; anything else is a plain text match.
                conditions = [location_matches(f"%{location}%")]
                conditions += [JobPosting.metros.contains([code]) for code in geo.search_areas(location) or []]
                stmt = stmt.where(or_(*conditions))
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
        # A posting often lists only one of salary_min/salary_max — compare
        # against whichever end of its range is actually set, falling back to
        # the other one, rather than requiring both. A posting with neither
        # set fails both comparisons (NULL >=/<= anything is NULL, which
        # WHERE treats as false), so an active salary filter also drops
        # postings with no pay listed at all — same as every other filter
        # here, which only ever narrows to postings that positively match.
        # Currencies aren't normalized: this compares raw numbers regardless
        # of salary_currency, acceptable while postings are overwhelmingly USD.
        if salary_min is not None:
            stmt = stmt.where(func.coalesce(JobPosting.salary_max, JobPosting.salary_min) >= salary_min)
        if salary_max is not None:
            stmt = stmt.where(func.coalesce(JobPosting.salary_min, JobPosting.salary_max) <= salary_max)

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
