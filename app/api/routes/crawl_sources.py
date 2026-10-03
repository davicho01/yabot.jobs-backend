import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user, get_db
from app.models.company import Company
from app.models.crawl_source import CrawlSource
from app.models.enums import CrawlSourceStatus, ScanStatus
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.schemas.admin import AdminCompanyRead, CrawlSourceStatsRead, ScanDayCount, ScanHourCount, ScanMonthCount, ScanWeekCount
from app.schemas.crawl_source import CrawlSourceCreate, CrawlSourceRead, CrawlSourceUpdate
from app.services import admin as admin_service
from app.services.ats_adapters import detect_ats_source, detect_embedded_ats_source
from app.services.company_logos import logo_url_for
from app.services.crawl_queue import enqueue_crawl, ensure_topic
from app.services.job_dedup import normalize_company_name
from app.services.job_queue import enqueue_source_scan
from app.services.job_queue import ensure_topic as ensure_scan_topic

router = APIRouter(prefix="/admin/crawl-sources", tags=["admin"], dependencies=[Depends(get_current_admin_user)])


@router.get("", response_model=list[CrawlSourceRead])
def list_crawl_sources(db: Session = Depends(get_db)) -> list[CrawlSourceRead]:
    sources = db.scalars(select(CrawlSource).order_by(CrawlSource.name)).all()
    return list(sources)


@router.post("", response_model=CrawlSourceRead, status_code=status.HTTP_201_CREATED)
def create_crawl_source(payload: CrawlSourceCreate, db: Session = Depends(get_db)) -> CrawlSource:
    try:
        ats_type, _ = detect_ats_source(payload.board_url)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    source = CrawlSource(name=payload.name, ats_type=ats_type, board_url=payload.board_url)
    db.add(source)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A crawl source for this board_url already exists.",
        ) from exc
    return source


@router.patch("/{source_id}", response_model=CrawlSourceRead)
def update_crawl_source(
    source_id: uuid.UUID, payload: CrawlSourceUpdate, db: Session = Depends(get_db)
) -> CrawlSource:
    source = db.get(CrawlSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")

    data = payload.model_dump(exclude_unset=True)
    new_status = data.get("status", source.status)
    new_ats_type = source.ats_type
    if "board_url" in data and (data["board_url"] != source.board_url or new_ats_type is None):
        try:
            new_ats_type, _ = detect_ats_source(data["board_url"])
        except ValueError as exc:
            # Same embedded fallback register_discovered_board uses —
            # white-label platforms (Clinch, Oracle Fusion, TalentBrew, ...)
            # have no static URL shape, so a "pending" row for one of them
            # can only ever resolve here, never through detect_ats_source
            # alone.
            embedded = detect_embedded_ats_source(data["board_url"])
            if embedded is None:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
            new_ats_type, _ = embedded
        data["ats_type"] = new_ats_type
    if new_status == CrawlSourceStatus.ACTIVE and not new_ats_type:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="status can only be 'active' once board_url resolves to a supported ats_type.",
        )

    for field, value in data.items():
        setattr(source, field, value)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A crawl source for this board_url already exists.",
        ) from exc
    return source


@router.post("/{source_id}/crawl", status_code=status.HTTP_202_ACCEPTED)
def trigger_crawl_source(source_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """Publish a one-off crawl request for a single source, same message
    crawl_dispatcher.py's daily fan-out sends — crawl_worker.py picks it up
    and processes it exactly like any scheduled dispatch. Lets an admin
    verify one company's adapter (or re-crawl after fixing it) without
    waiting for the next scheduled run or triggering every other active
    source too.
    """
    source = db.get(CrawlSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")
    if source.status != CrawlSourceStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only an active crawl source can be crawled.",
        )
    ensure_topic()
    enqueue_crawl(source.id)
    return {"queued": True}


@router.post("/{source_id}/rescan", status_code=status.HTTP_202_ACCEPTED)
def rescan_crawl_source(source_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """Re-queue a scan for every JobPostingUrl this source has discovered
    so far, e.g. after fixing/improving its ATS adapter — lets already-
    scanned postings pick up the fix immediately instead of only refreshing
    the next time each one happens to be re-crawled.

    Resets each row to PENDING before publishing: a scan lane only claims
    PENDING rows, so leaving scan_status at SUCCESS/FAILED would make it
    find nothing to do. The response's `queued` is the number of URLs reset;
    they're worked through at the source's max_concurrent_scans pace, not
    all at once.
    """
    source = db.get(CrawlSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")

    url_rows = db.scalars(select(JobPostingUrl).where(JobPostingUrl.crawl_source_id == source_id)).all()
    if not url_rows:
        return {"queued": 0}

    for url_row in url_rows:
        url_row.scan_status = ScanStatus.PENDING
        url_row.scan_error = None
        url_row.scan_claimed_at = None
        # A deliberate admin rescan earns a fresh retry budget too — same
        # reasoning as rescan_job_url — so a NEEDS_REVIEW row (out of
        # automatic retries) gets picked up here rather than staying stuck.
        url_row.scan_attempts = 0
        url_row.next_retry_at = None
    db.commit()  # committed, not just flushed — the worker reads url_row on a separate connection

    # One wake-up per allowed lane, not one message per URL: the source's
    # lanes work through the rows at its max_concurrent_scans pace.
    ensure_scan_topic()
    enqueue_source_scan(source_id, lanes=source.max_concurrent_scans)

    return {"queued": len(url_rows)}


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_crawl_source(source_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    source = db.get(CrawlSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")
    db.delete(source)


@router.get("/{source_id}/companies", response_model=list[AdminCompanyRead])
def list_crawl_source_companies(source_id: uuid.UUID, db: Session = Depends(get_db)) -> list[AdminCompanyRead]:
    """The companies this source's postings belong to (usually one; a
    multi-brand board like Walmart's has several), most postings first —
    what the Edit source dialog shows a logo field for. A source with no
    scanned postings yet still gets one entry, from its own name, so a logo
    can be set before the first crawl."""
    source = db.get(CrawlSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")
    rows = db.execute(
        select(JobPosting.company_key, func.max(JobPosting.company_name), func.count())
        .join(JobPostingUrl, JobPostingUrl.id == JobPosting.url_id)
        .where(JobPostingUrl.crawl_source_id == source_id, JobPosting.company_key.is_not(None))
        .group_by(JobPosting.company_key)
        .order_by(func.count().desc())
        .limit(10)
    ).all()
    if not rows:
        key = normalize_company_name(source.name)
        rows = [(key, source.name, 0)] if key else []
    companies = {
        c.company_key: c
        for c in db.scalars(select(Company).where(Company.company_key.in_([key for key, _, _ in rows])))
    }
    result = []
    for key, name, count in rows:
        company = companies.get(key)
        read = (
            AdminCompanyRead.model_validate(company)
            if company is not None
            else AdminCompanyRead(company_key=key, display_name=name, domain=None, domain_source=None)
        )
        read.logo_url = logo_url_for(company.logo_key) if company is not None else None
        read.posting_count = count
        result.append(read)
    return result


@router.get("/{source_id}/stats", response_model=CrawlSourceStatsRead)
def get_crawl_source_stats(source_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    source = db.get(CrawlSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")
    return admin_service.get_crawl_source_stats(db, source)


@router.get("/{source_id}/scans-by-day", response_model=list[ScanDayCount])
def get_crawl_source_scans_by_day(
    source_id: uuid.UUID, days: int = Query(90, ge=1, le=365), db: Session = Depends(get_db)
) -> list[dict]:
    source = db.get(CrawlSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")
    return admin_service.get_scans_by_day(db, days=days, crawl_source_id=source_id)


@router.get("/{source_id}/scans-by-hour", response_model=list[ScanHourCount])
def get_crawl_source_scans_by_hour(
    source_id: uuid.UUID,
    hours: int = Query(24, ge=1, le=168),
    end: datetime | None = Query(None),
    db: Session = Depends(get_db),
) -> list[dict]:
    source = db.get(CrawlSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")
    return admin_service.get_scans_by_hour(db, hours=hours, crawl_source_id=source_id, end=end)


@router.get("/{source_id}/scans-by-week", response_model=list[ScanWeekCount])
def get_crawl_source_scans_by_week(
    source_id: uuid.UUID, weeks: int = Query(26, ge=1, le=104), db: Session = Depends(get_db)
) -> list[dict]:
    source = db.get(CrawlSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")
    return admin_service.get_scans_by_week(db, weeks=weeks, crawl_source_id=source_id)


@router.get("/{source_id}/scans-by-month", response_model=list[ScanMonthCount])
def get_crawl_source_scans_by_month(
    source_id: uuid.UUID, months: int = Query(6, ge=1, le=60), db: Session = Depends(get_db)
) -> list[dict]:
    source = db.get(CrawlSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")
    return admin_service.get_scans_by_month(db, months=months, crawl_source_id=source_id)
