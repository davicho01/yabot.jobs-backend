import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ResumeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    is_main: bool
    created_at: datetime


class ResumeUpdate(BaseModel):
    is_main: bool | None = None


class HtmlContentUpload(BaseModel):
    # ATS-friendly fragment: h1-h3, p, ul/ol/li, strong/em only — see
    # app.services.resume_renderer.render_html_docx for what's honored.
    html: str


class ResumeReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    resume_id: uuid.UUID
    strengths: list[str]
    weaknesses: list[str]
    suggestions: list[str]
    summary: str
    created_at: datetime


class ResumeScoreRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    resume_id: uuid.UUID
    job_posting_id: uuid.UUID
    overall_score: int
    matched_keywords: list[str]
    missing_keywords: list[str]
    summary: str
    created_at: datetime


class TailoredResumeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    resume_id: uuid.UUID
    job_posting_id: uuid.UUID
    content: dict
    filename: str
    created_at: datetime


class CoverLetterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    resume_id: uuid.UUID
    job_posting_id: uuid.UUID
    content: dict
    filename: str
    created_at: datetime
