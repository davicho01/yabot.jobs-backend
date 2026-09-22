import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.user import UserRead


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkVerifyRequest(BaseModel):
    token: str


class UserUpdate(BaseModel):
    # A true partial update — see update_current_user, which only touches a
    # field when it was actually present in the request body (model_dump's
    # exclude_unset), not just non-None. Both fields default to None so
    # either can be omitted, but omitting one is different from explicitly
    # sending it as null/false: display_name: null clears the name (see the
    # route), and email_alerts_enabled has no null state to send at all
    # (it's just bool | None here so "not provided" is expressible).
    display_name: str | None = Field(default=None, max_length=120)
    email_alerts_enabled: bool | None = None


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
