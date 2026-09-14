import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.api_key import UserApiKey
from app.models.user import User
from app.schemas.api_key import ApiKeyCreate, ApiKeyRead, ApiKeyUpdate

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


_MASK_STARS = 8


def _mask(plaintext: str) -> str:
    if len(plaintext) <= 4:
        return "*" * len(plaintext)
    # Fixed number of stars regardless of key length — different providers'
    # keys vary wildly in length (Anthropic's are 100+ chars, others much
    # shorter), and scaling the mask to match made some rows dramatically
    # wider than others in the keys list.
    return f"{'*' * _MASK_STARS}{plaintext[-4:]}"


def _to_read(key: UserApiKey) -> ApiKeyRead:
    return ApiKeyRead(
        id=key.id,
        provider=key.provider,
        label=key.label,
        model=key.model,
        base_url=key.base_url,
        is_active=key.is_active,
        is_default=key.is_default,
        last_used_at=key.last_used_at,
        created_at=key.created_at,
        masked_key=_mask(key.get_plaintext_key()),
    )


def _clear_existing_default(db: Session, user_id: uuid.UUID, *, except_id: uuid.UUID | None = None) -> None:
    stmt = update(UserApiKey).where(UserApiKey.user_id == user_id, UserApiKey.is_default.is_(True))
    if except_id is not None:
        stmt = stmt.where(UserApiKey.id != except_id)
    db.execute(stmt.values(is_default=False))


@router.get("", response_model=list[ApiKeyRead])
def list_api_keys(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[ApiKeyRead]:
    keys = db.scalars(select(UserApiKey).where(UserApiKey.user_id == current_user.id)).all()
    return [_to_read(key) for key in keys]


@router.post("", response_model=ApiKeyRead, status_code=status.HTTP_201_CREATED)
def create_api_key(
    payload: ApiKeyCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyRead:
    if payload.is_default:
        _clear_existing_default(db, current_user.id)

    key = UserApiKey(
        user_id=current_user.id,
        provider=payload.provider,
        label=payload.label,
        model=payload.model,
        base_url=payload.base_url,
        is_default=payload.is_default,
    )
    key.set_plaintext_key(payload.api_key)
    db.add(key)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a key stored for this provider/label.",
        ) from exc
    return _to_read(key)


@router.patch("/{key_id}", response_model=ApiKeyRead)
def update_api_key(
    key_id: uuid.UUID,
    payload: ApiKeyUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyRead:
    key = db.scalar(
        select(UserApiKey).where(UserApiKey.id == key_id, UserApiKey.user_id == current_user.id)
    )
    if key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found.")

    updates = payload.model_dump(exclude_unset=True)
    if updates.get("is_default"):
        _clear_existing_default(db, current_user.id, except_id=key.id)
    for field, value in updates.items():
        setattr(key, field, value)
    db.flush()
    return _to_read(key)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_api_key(
    key_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    key = db.scalar(
        select(UserApiKey).where(UserApiKey.id == key_id, UserApiKey.user_id == current_user.id)
    )
    if key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found.")
    db.delete(key)
