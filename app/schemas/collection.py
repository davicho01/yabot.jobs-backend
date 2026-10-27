import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.services.collection_admin import Action


class AdminCollectionRead(BaseModel):
    id: uuid.UUID
    key: str
    collection_type: str
    status: str
    name: str
    url: str | None  # its page, while it has one
    indexable: bool
    qualification_score: float | None
    recommendation: str | None
    issues: list[str]
    # From its last scoring: open jobs and companies hiring then.
    open_jobs: int | None
    companies: int | None
    last_evaluated_at: datetime | None
    updated_at: datetime
    actions: list[str]  # what the admin can do from here (collection_admin)


class AdminCollectionEventRead(BaseModel):
    created_at: datetime
    previous_status: str | None
    new_status: str
    trigger: str
    reason: str
    actor_email: str | None


class AdminCollectionDetailRead(AdminCollectionRead):
    qualification: dict[str, Any] | None
    events: list[AdminCollectionEventRead]


class AdminCollectionListRead(BaseModel):
    items: list[AdminCollectionRead]
    total: int
    counts: dict[str, int]  # every collection by status, ignoring the filters


class AdminCollectionAction(BaseModel):
    action: Action
    reason: str = Field(min_length=1, max_length=500)


class AdminCollectionBulkAction(AdminCollectionAction):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class AdminCollectionBulkError(BaseModel):
    id: uuid.UUID
    detail: str


class AdminCollectionBulkResult(BaseModel):
    updated: list[AdminCollectionRead]
    errors: list[AdminCollectionBulkError]
