import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.crawl_source import CrawlSource
from app.models.enums import CrawlSourceStatus
from app.schemas.crawl_source import CrawlSourceCreate, CrawlSourceRead, CrawlSourceUpdate
from app.services.ats_adapters import detect_ats_source

router = APIRouter(prefix="/crawl-sources", tags=["crawl-sources"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[CrawlSourceRead])
def list_crawl_sources(db: Session = Depends(get_db)) -> list[CrawlSourceRead]:
    sources = db.scalars(select(CrawlSource).order_by(CrawlSource.name)).all()
    return list(sources)


@router.post("", response_model=CrawlSourceRead, status_code=status.HTTP_201_CREATED)
def create_crawl_source(payload: CrawlSourceCreate, db: Session = Depends(get_db)) -> CrawlSource:
    if payload.url:
        try:
            ats_type, board_token = detect_ats_source(payload.url)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    else:
        ats_type, board_token = payload.ats_type, payload.board_token

    source = CrawlSource(name=payload.name, ats_type=ats_type, board_token=board_token)
    db.add(source)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A crawl source for this ats_type/board_token already exists.",
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
    new_ats_type = data.get("ats_type", source.ats_type)
    new_board_token = data.get("board_token", source.board_token)
    if new_status == CrawlSourceStatus.ACTIVE and not (new_ats_type and new_board_token):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="status can only be 'active' once ats_type and board_token are set.",
        )

    for field, value in data.items():
        setattr(source, field, value)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A crawl source for this ats_type/board_token already exists.",
        ) from exc
    return source


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_crawl_source(source_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    source = db.get(CrawlSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")
    db.delete(source)
