import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import WorkplaceType


class SavedSearchCreate(BaseModel):
    """One column per GET /jobs query param worth persisting — see
    app.models.saved_search.SavedSearch."""

    name: str | None = None
    q: str | None = None
    location: str | None = None
    metro: str | None = None
    radius: int | None = None
    company: str | None = None
    posted_within_days: int | None = None
    workplace_type: WorkplaceType | None = None
    salary_min: int | None = None
    salary_max: int | None = None


class SavedSearchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str | None
    q: str | None
    location: str | None
    metro: str | None
    radius: int | None
    company: str | None
    posted_within_days: int | None
    workplace_type: str | None
    salary_min: int | None
    salary_max: int | None
    created_at: datetime
    last_alerted_at: datetime | None
