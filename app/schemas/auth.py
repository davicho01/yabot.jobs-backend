import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.user import UserRead


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkVerifyRequest(BaseModel):
    token: str


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)


class AuthResponse(BaseModel):
    user: UserRead


class PersonalAccessTokenCreate(BaseModel):
    label: str
    # None = never expires (until explicitly revoked).
    expires_in_days: int | None = None


class PersonalAccessTokenCreateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    expires_at: datetime | None
    created_at: datetime
    # Only ever returned here, at creation — not retrievable afterwards.
    token: str


class PersonalAccessTokenRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
