import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user, get_db
from app.models.crawl_source import CrawlSource
from app.models.enums import CrawlSourceStatus
from app.schemas.admin import CrawlSourceStatsRead, ScanDayCount
from app.schemas.crawl_source import CrawlSourceCreate, CrawlSourceRead, CrawlSourceUpdate
from app.services import admin as admin_service
from app.services.ats_adapters import detect_ats_source, detect_embedded_ats_source
from app.services.crawl_queue import enqueue_crawl, ensure_topic_and_subscription

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
    if "board_url" in data:
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


@router.get("/{source_id}/scans-by-day", response_model=list[ScanDayCount])
def get_crawl_source_scans_by_day(
    source_id: uuid.UUID, days: int = Query(90, ge=1, le=365), db: Session = Depends(get_db)
) -> list[dict]:
    source = db.get(CrawlSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")
    return admin_service.get_scans_by_day(db, days=days, crawl_source_id=source_id)
