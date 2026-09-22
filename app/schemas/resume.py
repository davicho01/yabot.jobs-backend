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


class ResumeEntryContent(BaseModel):
    # One job/degree/project block within a section — e.g. a single role
    # under "Experience". subtitle is typically "Company · Dates".
    title: str
    subtitle: str | None = None
    bullets: list[str] = []


class ResumeSectionContent(BaseModel):
    # Either flat `bullets` (right for Skills/Certifications) or a list of
    # `entries` (right for Experience/Education/Projects, one block per job/
    # degree/project) — see TAILOR_PROMPT for which shape the LLM picks per
    # section. Both default to [] so older stored content (flat bullets
    # only, no "entries" key) still validates unchanged.
    heading: str
    bullets: list[str] = []
    entries: list[ResumeEntryContent] = []


class ContactInfo(BaseModel):
    # All optional — extracted from the source resume text (see TAILOR_PROMPT /
    # COVER_LETTER_PROMPT) or supplied directly by an /upload caller. Missing
    # fields are filled in from the User row (name/email only) at render time —
    # see _resolve_contact in app.api.routes.resumes.
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin: str | None = None


class TailoredResumeUpload(BaseModel):
    # Structured content — matches what the frontend already expects on
    # TailoredResume.content (src/api/types.ts) and what render_tailored_resume_docx
    # renders. Used both as the /upload request body and (via generate_main_tailored_resume)
    # as this app's own LLM output shape, so both paths produce the same content shape.
    summary: str
    sections: list[ResumeSectionContent]
    contact: ContactInfo | None = None


class CoverLetterUpload(BaseModel):
    # Structured content — matches what the frontend already expects on
    # CoverLetter.content (src/api/types.ts) and what render_cover_letter_docx renders.
    greeting: str
    body_paragraphs: list[str]
    closing: str
    contact: ContactInfo | None = None


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


class ResumeScoreHistoryEntryRead(BaseModel):
    # One past scoring of this resume against a job — job_title/company_name
    # are flattened in here (not a nested JobPostingRead) since a history
    # row only ever needs a label plus somewhere to link to. url_id (not
    # job_posting_id) is what a history row links out to — GET/POST
    # /jobs/{url_id}/... and the ApplyPage route are keyed on the URL, the
    # same id every other "open this posting" link on the site already uses.
    id: uuid.UUID
    job_posting_id: uuid.UUID
    url_id: uuid.UUID
    job_title: str | None
    company_name: str | None
    overall_score: int
    missing_keywords: list[str]
    created_at: datetime


class RecurringMissingKeywordRead(BaseModel):
    # A keyword that has shown up in missing_keywords on 2+ separate scores
    # of this resume — see GET /resumes/{resume_id}/score-history.
    keyword: str
    count: int


class ResumeScoreHistoryRead(BaseModel):
    entries: list[ResumeScoreHistoryEntryRead]
    recurring_missing_keywords: list[RecurringMissingKeywordRead]


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


class InterviewQuestion(BaseModel):
    question: str
    category: str  # "behavioral" | "technical" | "role_specific"
    # How this specific candidate should answer it, referencing their own
    # resume — not a generic tip.
    approach: str


class InterviewPrepContent(BaseModel):
    likely_questions: list[InterviewQuestion] = []
    talking_points: list[str] = []
    questions_to_ask: list[str] = []


class InterviewPrepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    resume_id: uuid.UUID
    job_posting_id: uuid.UUID
    content: InterviewPrepContent
    created_at: datetime
