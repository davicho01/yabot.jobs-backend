import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import FeedbackKind, FeedbackStatus


class FeedbackCreate(BaseModel):
    kind: FeedbackKind = FeedbackKind.OTHER
    message: str = Field(min_length=1, max_length=5000)
    rating: int | None = Field(default=None, ge=1, le=5)
    page_url: str | None = Field(default=None, max_length=2048)


class FeedbackRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    message: str
    rating: int | None
    page_url: str | None
    status: str
    created_at: datetime


class AdminFeedbackRead(FeedbackRead):
    user_id: uuid.UUID
    user_email: str
    user_agent: str | None


class AdminFeedbackListRead(BaseModel):
    items: list[AdminFeedbackRead]
    total: int
    new_count: int


class FeedbackStatusUpdate(BaseModel):
    status: FeedbackStatus
