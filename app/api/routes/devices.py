from typing import Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.services import push as push_service

router = APIRouter(prefix="/devices", tags=["devices"])


class DeviceRegister(BaseModel):
    # An Expo push token, e.g. "ExponentPushToken[xxxx]".
    token: str = Field(min_length=10, max_length=255)
    platform: Literal["ios", "android"]


class DeviceUnregister(BaseModel):
    token: str = Field(min_length=10, max_length=255)


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
def register_device(
    payload: DeviceRegister,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    push_service.register_device(db, current_user.id, payload.token, payload.platform)


@router.post("/unregister", status_code=status.HTTP_204_NO_CONTENT)
def unregister_device(
    payload: DeviceUnregister,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    push_service.unregister_device(db, current_user.id, payload.token)
