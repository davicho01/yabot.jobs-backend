import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.models.saved_search import SavedSearch
from app.models.user import User
from app.schemas.saved_search import SavedSearchCreate, SavedSearchRead

router = APIRouter(prefix="/saved-searches", tags=["saved-searches"])


@router.get("", response_model=list[SavedSearchRead])
def list_saved_searches(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[SavedSearch]:
    return db.scalars(
        select(SavedSearch)
        .where(SavedSearch.user_id == current_user.id)
        .order_by(SavedSearch.created_at.desc())
    ).all()


@router.post("", response_model=SavedSearchRead, status_code=status.HTTP_201_CREATED)
def create_saved_search(
    payload: SavedSearchCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SavedSearch:
    existing_count = db.scalar(
        select(func.count()).select_from(SavedSearch).where(SavedSearch.user_id == current_user.id)
    )
    if existing_count >= settings.saved_search_max_per_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You can save up to {settings.saved_search_max_per_user} searches — remove one first.",
        )
    saved_search = SavedSearch(user_id=current_user.id, **payload.model_dump())
    db.add(saved_search)
    db.flush()
    return saved_search


@router.delete("/{saved_search_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved_search(
    saved_search_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    saved_search = db.scalar(
        select(SavedSearch).where(SavedSearch.id == saved_search_id, SavedSearch.user_id == current_user.id)
    )
    if saved_search is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved search not found.")
    db.delete(saved_search)
