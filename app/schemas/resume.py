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


class ResumeDetailRead(ResumeRead):
    # Adds the extracted text on top of ResumeRead's metadata-only fields —
    # for callers (e.g. an MCP client's own LLM) who need the actual resume
    # content to evaluate/tailor against, not just its filename.
    parsed_text: str


class ResumeSectionContent(BaseModel):
    heading: str
    bullets: list[str]


class TailoredResumeUpload(BaseModel):
    # Structured content — matches what the frontend already expects on
    # TailoredResume.content (src/api/types.ts) and what render_tailored_resume_docx
    # renders. Used both as the /upload request body and (via generate_main_tailored_resume)
    # as this app's own LLM output shape, so both paths produce the same content shape.
    summary: str
    sections: list[ResumeSectionContent]


class CoverLetterUpload(BaseModel):
    # Structured content — matches what the frontend already expects on
    # CoverLetter.content (src/api/types.ts) and what render_cover_letter_docx renders.
    greeting: str
    body_paragraphs: list[str]
    closing: str


class ResumeReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    resume_id: uuid.UUID
    strengths: list[str]
    weaknesses: list[str]
    suggestions: list[str]
    summary: str
    created_at: datetime


class ResumeScoreUpload(BaseModel):
    # Same shape as ResumeScoreRead's LLM-derived fields — for callers (e.g.
    # an MCP client's own LLM) who've already evaluated fit themselves and
    # just want it stored, skipping this app's own LLM call.
    overall_score: int
    matched_keywords: list[str]
    missing_keywords: list[str]
    summary: str


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
    content: TailoredResumeUpload
    filename: str
    created_at: datetime


class TailoredResumeScoreUpload(BaseModel):
    # Same shape as ResumeScoreUpload — for callers (e.g. an MCP client's
    # own LLM) who've already scored a tailored resume's fit themselves and
    # just want it stored, skipping this app's own LLM call.
    overall_score: int
    matched_keywords: list[str]
    missing_keywords: list[str]
    summary: str


class TailoredResumeScoreRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tailored_resume_id: uuid.UUID
    job_posting_id: uuid.UUID
    overall_score: int
    matched_keywords: list[str]
    missing_keywords: list[str]
    summary: str
    created_at: datetime


class CoverLetterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    resume_id: uuid.UUID
    job_posting_id: uuid.UUID
    content: CoverLetterUpload
    filename: str
    created_at: datetime
