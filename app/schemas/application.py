import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, HttpUrl

from app.models.enums import ApplicationStatus


class ApplicationCreate(BaseModel):
    url: HttpUrl


class ApplicationUpdate(BaseModel):
    status: ApplicationStatus | None = None
    notes: str | None = None
    is_archived: bool | None = None


class ApplicationJobPostingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url_id: uuid.UUID
    apply_url: str
    title: str | None
    company_name: str | None
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    posted_at: date | None


class ApplicationTailoredResumeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str


class ApplicationCoverLetterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str


class ApplicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    notes: str | None
    is_archived: bool
    # When this row was first made — saving/applying to a job always adds one
    # (see create_application, ensure_user_applicant), so this is never null.
    created_at: datetime
    job_posting: ApplicationJobPostingRead
    # Max of latest_score/latest_tailored_resume_score — see
    # UserJobApplication.best_score in app/models/job_application.py.
    best_score: int | None
    latest_tailored_resume: ApplicationTailoredResumeRead | None
    latest_cover_letter: ApplicationCoverLetterRead | None
