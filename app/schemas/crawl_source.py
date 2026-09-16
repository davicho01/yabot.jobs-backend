import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import CrawlSourceStatus

_VALID_STATUSES = {v.value for v in CrawlSourceStatus}


class CrawlSourceCreate(BaseModel):
    name: str = Field(min_length=1)
    # The board's canonical URL — ats_type (and whatever internal
    # identifier that platform's adapter needs) is auto-detected from its
    # shape, see app.services.ats_adapters.detect_ats_source. Always
    # creates an "active" board; fails with 422 if the platform can't be
    # detected/supported, instead of leaving a placeholder row (unlike
    # boards auto-registered from a single job URL via POST /jobs, which
    # fall back to "pending").
    board_url: str = Field(min_length=1)


class CrawlSourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    # Set this (together with status="active") to promote a "pending"
    # board once an adapter for it has been verified and implemented — or
    # set status="rejected" alone if it turns out unsupportable. ats_type
    # is re-derived from the new board_url automatically.
    board_url: str | None = None
    status: str | None = None

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str | None) -> str | None:
        if value is not None and value not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {sorted(_VALID_STATUSES)}")
        return value


class CrawlSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    ats_type: str | None
    board_url: str
    status: str
    last_crawled_at: datetime | None
    last_error: str | None
    created_at: datetime
