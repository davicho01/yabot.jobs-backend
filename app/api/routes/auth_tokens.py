import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.auth import (
    PersonalAccessTokenCreate,
    PersonalAccessTokenCreateResponse,
    PersonalAccessTokenRead,
)
from app.services import auth as auth_service

router = APIRouter(prefix="/auth/tokens", tags=["auth"])


@router.post("", response_model=PersonalAccessTokenCreateResponse, status_code=status.HTTP_201_CREATED)
def create_personal_access_token(
    payload: PersonalAccessTokenCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PersonalAccessTokenCreateResponse:
    token, raw_token = auth_service.create_personal_access_token(
        db, current_user, payload.label, payload.expires_in_days
    )
    return PersonalAccessTokenCreateResponse(
        id=token.id,
        label=token.label,
        expires_at=token.expires_at,
        created_at=token.created_at,
        token=raw_token,
    )


@router.get("", response_model=list[PersonalAccessTokenRead])
def list_personal_access_tokens(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[PersonalAccessTokenRead]:
    return auth_service.list_personal_access_tokens(db, current_user.id)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_personal_access_token(
    token_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    token = auth_service.revoke_personal_access_token(db, current_user.id, token_id)
    if token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found.")
