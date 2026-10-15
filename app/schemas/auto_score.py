import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AutoScoreRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_posting_id: uuid.UUID
    resume_id: uuid.UUID
    # See app.models.enums.AutoScoreStatus.
    status: str
    resume_score: int | None
    tailored_score: int | None
    best_score: int | None
    error: str | None
    created_at: datetime
    updated_at: datetime
