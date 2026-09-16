import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user, get_db
from app.models.crawl_source import CrawlSource
from app.models.enums import CrawlSourceStatus, ScanStatus
from app.models.job_url import JobPostingUrl
from app.schemas.admin import CrawlSourceStatsRead, ScanDayCount
from app.schemas.crawl_source import CrawlSourceCreate, CrawlSourceRead, CrawlSourceUpdate
from app.schemas.job import JobListRead
from app.services import admin as admin_service
from app.services.ats_adapters import detect_ats_source, detect_embedded_ats_source
from app.services.crawl_queue import enqueue_crawl, ensure_topic_and_subscription
from app.services.job_queue import enqueue_scan
from app.services.job_queue import ensure_topic_and_subscription as ensure_scan_topic_and_subscription
from app.services.jobs import to_job_detail

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
    if "board_url" in data and data["board_url"] != source.board_url:
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
    ensure_topic_and_subscription()
    enqueue_crawl(source.id)
    return {"queued": True}


@router.post("/{source_id}/rescan", status_code=status.HTTP_202_ACCEPTED)
def rescan_crawl_source(source_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """Re-queue a scan for every JobPostingUrl this source has discovered
    so far, e.g. after fixing/improving its ATS adapter — lets already-
    scanned postings pick up the fix immediately instead of only refreshing
    the next time each one happens to be re-crawled.

    Resets each row to PENDING before publishing: process_scan_job (the
    same handler a fresh submission's scan request hits) no-ops on a
    non-PENDING url_row, so leaving scan_status at SUCCESS/FAILED would make
    the worker just skip every message this enqueues.
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
    db.commit()  # committed, not just flushed — the worker reads url_row on a separate connection

    ensure_scan_topic_and_subscription()
    for url_row in url_rows:
        enqueue_scan(url_row.id)

    return {"queued": len(url_rows)}


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_crawl_source(source_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    source = db.get(CrawlSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")
    db.delete(source)


@router.get("/{source_id}/stats", response_model=CrawlSourceStatsRead)
def get_crawl_source_stats(source_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    source = db.get(CrawlSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")
    return admin_service.get_crawl_source_stats(db, source)


@router.get("/{source_id}/jobs", response_model=JobListRead)
def get_crawl_source_jobs(
    source_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JobListRead:
    source = db.get(CrawlSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")

    stmt = select(JobPostingUrl).where(JobPostingUrl.crawl_source_id == source_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(JobPostingUrl.created_at.desc()).limit(page_size).offset((page - 1) * page_size)
    url_rows = db.scalars(stmt).all()
    return JobListRead(items=[to_job_detail(row) for row in url_rows], total=total, page=page, page_size=page_size)


@router.get("/{source_id}/scans-by-day", response_model=list[ScanDayCount])
def get_crawl_source_scans_by_day(
    source_id: uuid.UUID, days: int = Query(90, ge=1, le=365), db: Session = Depends(get_db)
) -> list[dict]:
    source = db.get(CrawlSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")
    return admin_service.get_scans_by_day(db, days=days, crawl_source_id=source_id)
