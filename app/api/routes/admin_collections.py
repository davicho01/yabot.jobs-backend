"""Admin review of job collections (PR-11, PR-14): the list with scores,
reasons and likely duplicates, one collection's history, approving,
no-indexing, archiving or restoring one or a batch
(app.services.collection_admin), re-scoring one now
(app.services.collection_lifecycle), and previewing one's page."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func, nulls_last, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_admin_user, get_db
from app.models.enums import CollectionStatus, CollectionType
from app.models.job_collection import JobCollection
from app.models.user import User
from app.schemas.collection import (
    AdminCollectionAction,
    AdminCollectionBulkAction,
    AdminCollectionBulkError,
    AdminCollectionBulkResult,
    AdminCollectionDetailRead,
    AdminCollectionEventRead,
    AdminCollectionListRead,
    AdminCollectionRead,
)
from app.services import collection_admin, collection_dimensions, collection_lifecycle, static_collection_pages
from app.services.job_collections import CollectionError
from app.services.static_collection_pages import LIVE_STATUSES, RENDERED_TYPES, collection_path
from app.services.static_hub_pages import _url

router = APIRouter(prefix="/admin/collections", tags=["admin"], dependencies=[Depends(get_current_admin_user)])


def _to_read(collection: JobCollection) -> dict:
    qualification = collection.qualification or {}
    stats = qualification.get("stats") or {}
    has_page = collection.status in LIVE_STATUSES and collection.collection_type in RENDERED_TYPES
    return dict(
        id=collection.id,
        key=collection.key,
        collection_type=collection.collection_type,
        status=collection.status,
        name=collection_admin.collection_name(collection),
        url=_url(collection_path(collection)) if has_page else None,
        path=f"/{collection_path(collection)}" if collection.collection_type in RENDERED_TYPES else None,
        indexable=collection.is_indexable,
        qualification_score=collection.qualification_score,
        recommendation=qualification.get("recommendation"),
        issues=list(qualification.get("issues") or []),
        open_jobs=stats.get("open_jobs"),
        companies=stats.get("companies_hiring"),
        freshness_score=qualification.get("freshness_score"),
        duplicates=list(qualification.get("duplicates") or []),
        below_bar_since=qualification.get("below_bar_since"),
        empty_since=qualification.get("empty_since"),
        last_evaluated_at=collection.last_evaluated_at,
        updated_at=collection.updated_at,
        actions=collection_admin.available_actions(collection),
        can_reevaluate=collection.status != CollectionStatus.ARCHIVED
        and collection.role is not None and collection_dimensions.get("role", collection.role) is not None,
    )


def _to_detail(db: Session, collection: JobCollection) -> AdminCollectionDetailRead:
    actors = {e.actor_user_id for e in collection.events if e.actor_user_id}
    emails = dict(db.execute(select(User.id, User.email).where(User.id.in_(actors))).all()) if actors else {}
    events = [
        AdminCollectionEventRead(
            created_at=e.created_at, previous_status=e.previous_status, new_status=e.new_status, trigger=e.trigger,
            reason=e.reason, actor_email=emails.get(e.actor_user_id),
        )
        for e in reversed(collection.events)  # newest first
    ]
    return AdminCollectionDetailRead(**_to_read(collection), qualification=collection.qualification, events=events)


def _get(db: Session, collection_id: uuid.UUID) -> JobCollection:
    collection = db.scalar(
        select(JobCollection).where(JobCollection.id == collection_id).options(selectinload(JobCollection.events))
    )
    if collection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found.")
    return collection


@router.get("", response_model=AdminCollectionListRead)
def list_collections(
    view: collection_admin.View = Query("waiting"),
    collection_type: CollectionType | None = Query(None, alias="type"),
    q: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> AdminCollectionListRead:
    """Highest score first. `counts` ignores the filters, so the page can
    always show how many are waiting."""
    stmt = select(JobCollection)
    if (statuses := collection_admin.VIEWS[view]) is not None:
        stmt = stmt.where(JobCollection.status.in_(statuses))
    if collection_type is not None:
        stmt = stmt.where(JobCollection.collection_type == collection_type)
    if q:
        stmt = stmt.where(JobCollection.key.ilike(f"%{q.strip().lower().replace(' ', '-')}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(nulls_last(JobCollection.qualification_score.desc()), JobCollection.key)
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    counts = dict(db.execute(select(JobCollection.status, func.count()).group_by(JobCollection.status)).all())
    return AdminCollectionListRead(items=[_to_read(c) for c in rows], total=total, counts=counts)


@router.get("/{collection_id}", response_model=AdminCollectionDetailRead)
def get_collection(collection_id: uuid.UUID, db: Session = Depends(get_db)) -> AdminCollectionDetailRead:
    return _to_detail(db, _get(db, collection_id))


@router.get("/{collection_id}/preview", response_class=HTMLResponse)
def preview_collection(collection_id: uuid.UUID, db: Session = Depends(get_db)) -> HTMLResponse:
    """Its page as the generator would render it now (page 1), from its open
    jobs in the database, published or not."""
    collection = _get(db, collection_id)
    if collection.collection_type not in RENDERED_TYPES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This kind of collection has no page.")
    page = static_collection_pages.preview_page(db, collection, datetime.now(timezone.utc))
    if page is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No open jobs right now, so no page.")
    return HTMLResponse(static_collection_pages.render_collection_page(page, 1))


@router.post("/{collection_id}/actions", response_model=AdminCollectionDetailRead)
def act_on_collection(
    collection_id: uuid.UUID,
    payload: AdminCollectionAction,
    admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> AdminCollectionDetailRead:
    collection = _get(db, collection_id)
    try:
        collection_admin.apply_action(db, collection, payload.action, admin=admin, reason=payload.reason)
    except CollectionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.flush()
    db.refresh(collection, ["events"])
    return _to_detail(db, collection)


@router.post("/{collection_id}/reevaluate", response_model=AdminCollectionDetailRead)
def reevaluate_collection(collection_id: uuid.UUID, db: Session = Depends(get_db)) -> AdminCollectionDetailRead:
    """Re-score it now from its open jobs, applying the lifecycle rules as
    the nightly run would (collection_lifecycle)."""
    collection = _get(db, collection_id)
    if collection.status == CollectionStatus.ARCHIVED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Restore it first: archived ones aren't scored.")
    try:
        collection_lifecycle.reevaluate_one(db, collection, datetime.now(timezone.utc))
    except CollectionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.refresh(collection, ["events"])
    return _to_detail(db, collection)


@router.post("/bulk", response_model=AdminCollectionBulkResult)
def act_on_collections(
    payload: AdminCollectionBulkAction,
    admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> AdminCollectionBulkResult:
    """The same action and reason on each; one that can't take it (already
    there, or gone) is reported and the rest still go through."""
    found = {c.id: c for c in db.scalars(select(JobCollection).where(JobCollection.id.in_(payload.ids)))}
    updated: list[JobCollection] = []
    errors = []
    for collection_id in dict.fromkeys(payload.ids):
        collection = found.get(collection_id)
        if collection is None:
            errors.append(AdminCollectionBulkError(id=collection_id, detail="Collection not found."))
            continue
        try:  # refused before any change (collection_admin.apply_action)
            collection_admin.apply_action(db, collection, payload.action, admin=admin, reason=payload.reason)
        except CollectionError as exc:
            errors.append(AdminCollectionBulkError(id=collection_id, detail=str(exc)))
            continue
        updated.append(collection)
    db.flush()
    return AdminCollectionBulkResult(updated=[AdminCollectionRead(**_to_read(c)) for c in updated], errors=errors)
