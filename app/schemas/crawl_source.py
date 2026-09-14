import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import AtsType, CrawlSourceStatus

_VALID_ATS_TYPES = {v.value for v in AtsType}
_VALID_STATUSES = {v.value for v in CrawlSourceStatus}


class CrawlSourceCreate(BaseModel):
    name: str = Field(min_length=1)
    # Either provide `url` (a careers/job URL for the board — the ats_type
    # and board_token are auto-detected from its shape, see
    # app.services.ats_adapters.detect_ats_source) or provide `ats_type` +
    # `board_token` explicitly. Exactly one of the two forms is required.
    # Either way this endpoint always creates an "active" board — if the
    # platform can't be detected/supported, creation fails with a 422
    # instead of leaving a placeholder row (unlike boards auto-registered
    # from a single job URL via POST /jobs, which fall back to "pending").
    url: str | None = None
    ats_type: str | None = None
    board_token: str | None = None

    @field_validator("ats_type")
    @classmethod
    def _validate_ats_type(cls, value: str | None) -> str | None:
        if value is not None and value not in _VALID_ATS_TYPES:
            raise ValueError(f"ats_type must be one of {sorted(_VALID_ATS_TYPES)}")
        return value

    @model_validator(mode="after")
    def _require_url_or_explicit_fields(self) -> "CrawlSourceCreate":
        has_url = bool(self.url)
        has_explicit = bool(self.ats_type and self.board_token)
        if has_url and has_explicit:
            raise ValueError("Provide either 'url', or 'ats_type' + 'board_token' — not both.")
        if not has_url and not has_explicit:
            raise ValueError("Provide either 'url', or both 'ats_type' and 'board_token'.")
        return self


class CrawlSourceUpdate(BaseModel):
    is_active: bool | None = None
    # Fill these in (together with status="active") to promote a "pending"
    # board once an adapter for it has been verified and implemented — or
    # set status="rejected" alone if it turns out unsupportable.
    ats_type: str | None = None
    board_token: str | None = None
    status: str | None = None

    @field_validator("ats_type")
    @classmethod
    def _validate_ats_type(cls, value: str | None) -> str | None:
        if value is not None and value not in _VALID_ATS_TYPES:
            raise ValueError(f"ats_type must be one of {sorted(_VALID_ATS_TYPES)}")
        return value

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
    board_token: str | None
    detected_domain: str | None
    status: str
    is_active: bool
    last_crawled_at: datetime | None
    last_job_count: int | None
    last_error: str | None
    created_at: datetime
