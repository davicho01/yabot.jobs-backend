import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, HttpUrl

from app.models.enums import ApplicationStatus
from app.schemas.job import JobPostingRead
from app.schemas.resume import CoverLetterRead, ResumeScoreRead, TailoredResumeRead


class ApplicationCreate(BaseModel):
    url: HttpUrl


class ApplicationUpdate(BaseModel):
    status: ApplicationStatus | None = None
    notes: str | None = None
    is_archived: bool | None = None


class ApplicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    applied_at: datetime | None
    notes: str | None
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    job_posting: JobPostingRead
    # Names match the UserJobApplication relationship attributes directly
    # (see app/models/job_application.py) so from_attributes needs no alias.
    latest_score: ResumeScoreRead | None
    latest_tailored_resume: TailoredResumeRead | None
    latest_cover_letter: CoverLetterRead | None
