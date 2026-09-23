import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, HttpUrl

from app.models.enums import ApplicationStatus


class ApplicationCreate(BaseModel):
    url: HttpUrl


class ApplicationUpdate(BaseModel):
    # A genuine partial update (see update_application) — a field is only
    # touched when the request actually included it (exclude_unset), not
    # whenever it's non-None. That distinction matters most for
    # follow_up_at: sending it as null is a deliberate "clear the reminder",
    # different from just not mentioning it.
    status: ApplicationStatus | None = None
    notes: str | None = None
    is_archived: bool | None = None
    follow_up_at: date | None = None
    # Same partial-update rule as follow_up_at: explicit null clears the
    # pick (falls back to is_main again), omitting the field leaves
    # whatever's already stored alone.
    selected_resume_id: uuid.UUID | None = None


class ApplicationBulkUpdate(BaseModel):
    # Neither field needs follow_up_at/selected_resume_id-style null-vs-
    # omitted tracking (see ApplicationUpdate) — a bulk action only ever
    # sets status or flips is_archived, never deliberately clears either
    # back to "unset", so a plain None-means-"leave alone" default is enough.
    ids: list[uuid.UUID]
    status: ApplicationStatus | None = None
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
    # Set the moment status first transitions to "applied" (see
    # update_application) — null until then. Lets the apply page tell the
    # candidate exactly when they marked this applied.
    applied_at: datetime | None
    follow_up_at: date | None
    # Which resume the apply page's picker is set to for this application —
    # null means no explicit pick (frontend falls back to is_main).
    selected_resume_id: uuid.UUID | None
    job_posting: ApplicationJobPostingRead
    # Max of latest_score/latest_tailored_resume_score — see
    # UserJobApplication.best_score in app/models/job_application.py.
    best_score: int | None
    latest_tailored_resume: ApplicationTailoredResumeRead | None
    latest_cover_letter: ApplicationCoverLetterRead | None
