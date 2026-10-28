import uuid
from typing import Literal

from pydantic import BaseModel, Field


class JobShareCreate(BaseModel):
    id: str = Field(min_length=16, max_length=32)
    job_url_id: uuid.UUID
    method: Literal["share_sheet", "copy_link"]


class AttributionCreate(BaseModel):
    """What the app kept from the first visit (see app.services.job_shares)."""

    share_id: str | None = Field(None, max_length=32)
    source: str | None = Field(None, max_length=500)
    medium: str | None = Field(None, max_length=500)
    campaign: str | None = Field(None, max_length=500)
    landing_page: str | None = Field(None, max_length=2000)


class AttributionRead(BaseModel):
    recorded: bool
