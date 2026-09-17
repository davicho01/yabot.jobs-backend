import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, HttpUrl


class JobUrlSubmit(BaseModel):
    url: HttpUrl


class JobPostingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url_id: uuid.UUID
    apply_url: str
    title: str | None
    company_name: str | None
    location: str | None
    workplace_type: str
    employment_type: str
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    description: str | None
    extracted_fields: dict[str, Any] | None
    posted_at: date | None
    scanned_at: datetime | None
    extraction_status: str


class JobPostingUrlRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    domain: str
    scan_status: str
    scan_error: str | None
    crawl_source_id: uuid.UUID | None
    last_scanned_at: datetime | None
    created_at: datetime


class JobDetailRead(BaseModel):
    """A URL plus its current (most recent) extracted posting, if any."""

    url: JobPostingUrlRead
    posting: JobPostingRead | None


class JobListRead(BaseModel):
    items: list[JobDetailRead]
    total: int
    page: int
    page_size: int
