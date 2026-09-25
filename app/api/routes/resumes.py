import logging
import uuid
from collections import Counter, defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, get_db
from app.models.job_application import UserJobApplication
from app.models.job_posting import JobPosting
from app.models.resume import (
    CoverLetter,
    InterviewPrep,
    Resume,
    ResumeReview,
    ResumeScore,
    ResumeSkillAddition,
    TailoredResume,
    TailoredResumeScore,
)
from app.models.user import User
from app.schemas.resume import (
    ContactInfo,
    CoverLetterRead,
    CoverLetterUpload,
    InterviewPrepRead,
    MissingSkillJobRef,
    MissingSkillSummaryEntry,
    MissingSkillsSummaryRead,
    RecurringMissingKeywordRead,
    ResumeDetailRead,
    ResumeRead,
    ResumeReviewRead,
    ResumeRolesRead,
    ResumeScoreHistoryEntryRead,
    ResumeScoreHistoryRead,
    ResumeScoreRead,
    ResumeScoreUpload,
    ResumeSectionContent,
    ResumeSkillAdditionRead,
    ResumeSkillAdditionUpsert,
    ResumeUpdate,
    TailoredResumeRead,
    TailoredResumeScoreRead,
    TailoredResumeScoreUpload,
    TailoredResumeUpload,
)
from app.services.llm_client import LlmError
from app.services.resume_llm import (
    apply_skill_additions_with_llm,
    evaluate_resume_with_llm,
    extract_resume_roles_with_llm,
    extract_resume_structure_with_llm,
    generate_cover_letter_with_llm,
    generate_interview_prep_with_llm,
    generate_tailored_resume_with_llm,
    get_users_default_llm_key,
    quick_score_resume_with_llm,
    review_resume_with_llm,
)
from app.services.resume_parser import SUPPORTED_CONTENT_TYPES, extract_text
from app.services.resume_renderer import (
    render_cover_letter_docx,
    render_cover_letter_pdf,
    render_tailored_resume_docx,
    render_tailored_resume_pdf,
)
from app.services.resume_storage import delete_file, download_file, upload_file

logger = logging.getLogger("app.resumes")

router = APIRouter(prefix="/resumes", tags=["resumes"])

_MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
_TAILORED_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PDF_CONTENT_TYPE = "application/pdf"


def _clear_existing_main(db: Session, user_id: uuid.UUID, *, except_id: uuid.UUID | None = None) -> None:
    stmt = update(Resume).where(Resume.user_id == user_id, Resume.is_main.is_(True))
    if except_id is not None:
        stmt = stmt.where(Resume.id != except_id)
    db.execute(stmt.values(is_main=False))


def _get_main_resume(db: Session, user_id: uuid.UUID) -> Resume:
    resume = db.scalar(select(Resume).where(Resume.user_id == user_id, Resume.is_main.is_(True)))
    if resume is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="No main resume set. Upload a resume, or PATCH one to is_main=true, first.",
        )
    return resume


def _resolve_resume(db: Session, user_id: uuid.UUID, resume_id: uuid.UUID | None) -> Resume:
    """Which resume a review/score/tailored-resume/cover-letter call against:
    an explicit resume_id when the caller supplies one (the frontend's
    ApplyPage resume picker — see #8), otherwise this user's main resume,
    the only option before resume_id existed here. Callers that never send
    resume_id (the MCP server included) keep getting exactly that same
    fallback, unchanged.
    """
    if resume_id is not None:
        resume = db.scalar(select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id))
        if resume is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found.")
        return resume
    return _get_main_resume(db, user_id)


def _get_job_posting(db: Session, job_posting_id: uuid.UUID) -> JobPosting:
    posting = db.get(JobPosting, job_posting_id)
    if posting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job posting not found.")
    return posting


def _get_owned_tailored_resume(db: Session, user_id: uuid.UUID, tailored_id: uuid.UUID) -> TailoredResume:
    tailored = db.scalar(
        select(TailoredResume).where(TailoredResume.id == tailored_id, TailoredResume.user_id == user_id)
    )
    if tailored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tailored resume not found.")
    return tailored


def _tailored_resume_text(content: dict) -> str:
    """Flatten a TailoredResume's structured content (see TailoredResumeUpload)
    back into plain text — originally just for re-scoring, but also stored
    as Resume.parsed_text for a skill-additions-derived resume (see
    apply_resume_skill_additions), so this needs to be a faithful full
    rendering, not just what a scorer cares about. A section uses exactly
    one of "bullets" or "entries" (see TAILOR_PROMPT) — entries (one per
    job/degree/project, e.g. "Professional Experience") must be rendered
    too, or the scorer sees nothing but section headings for any resume
    with real work history and drastically under-scores it.
    """
    contact = content.get("contact") or {}
    contact_line = " | ".join(
        contact[key] for key in ("email", "phone", "location", "linkedin") if contact.get(key)
    )
    lines = [line for line in (contact.get("name"), contact_line) if line]
    lines.append(content.get("summary", ""))
    for section in content.get("sections", []):
        lines.append(section.get("heading", ""))
        lines.extend(f"- {b}" for b in section.get("bullets", []))
        for entry in section.get("entries", []):
            header = entry.get("title", "")
            if entry.get("subtitle"):
                header = f"{header} — {entry['subtitle']}"
            lines.append(header)
            lines.extend(f"- {b}" for b in entry.get("bullets", []))
    return "\n".join(lines)


def _sections_tuples(sections: list[dict]) -> list[tuple[str, list[str], list[dict]]]:
    """Turn a TailoredResumeUpload-shaped `sections` list (as stored raw in
    JSONB — TailoredResume.content, CoverLetter.content isn't sectioned, or
    Resume.structured_content) into the (heading, bullets, entries) tuples
    render_tailored_resume_docx/render_tailored_resume_pdf expect.
    """
    return [(section.get("heading", ""), section.get("bullets", []), section.get("entries", [])) for section in sections]


def _render_tailored_content(content: dict, contact: dict[str, str], fmt: Literal["docx", "pdf"]) -> tuple[bytes, str]:
    """Render a TailoredResumeUpload-shaped dict (TailoredResume.content or
    Resume.structured_content) to bytes in the requested format, returning
    (bytes, content_type).
    """
    summary = content.get("summary", "")
    sections = _sections_tuples(content.get("sections", []))
    if fmt == "pdf":
        return render_tailored_resume_pdf(summary, sections, contact), _PDF_CONTENT_TYPE
    return render_tailored_resume_docx(summary, sections, contact), _TAILORED_CONTENT_TYPE


def _render_cover_letter_content(content: dict, contact: dict[str, str], fmt: Literal["docx", "pdf"]) -> tuple[bytes, str]:
    """Render a CoverLetterUpload-shaped dict (CoverLetter.content) to bytes
    in the requested format, returning (bytes, content_type).
    """
    greeting = content.get("greeting", "")
    body_paragraphs = content.get("body_paragraphs", [])
    closing = content.get("closing", "")
    if fmt == "pdf":
        return render_cover_letter_pdf(greeting, body_paragraphs, closing, contact), _PDF_CONTENT_TYPE
    return render_cover_letter_docx(greeting, body_paragraphs, closing, contact), _TAILORED_CONTENT_TYPE


def _stamp_application_pointer(
    db: Session, user_id: uuid.UUID, job_posting_id: uuid.UUID, **pointer: uuid.UUID
) -> None:
    """Record the just-generated score/tailored-resume/cover-letter as the
    current one on this user's application for the job, get-or-creating the
    application itself so the pointer always has somewhere to live even if
    the frontend's own save call hasn't landed yet. `pointer` is a single
    `latest_score_id=`/`latest_tailored_resume_id=`/`latest_cover_letter_id=`
    kwarg — enforced by the one caller each below, not by this signature.
    """
    application = db.scalar(
        select(UserJobApplication).where(
            UserJobApplication.user_id == user_id, UserJobApplication.job_posting_id == job_posting_id
        )
    )
    if application is None:
        application = UserJobApplication(user_id=user_id, job_posting_id=job_posting_id)
        db.add(application)
    for field, value in pointer.items():
        setattr(application, field, value)
    db.flush()


def _structure_resume(db: Session, current_user: User, resume: Resume) -> dict:
    """Structure a resume's own content via the LLM (see
    extract_resume_structure_with_llm) and store it on resume.structured_content
    — same TailoredResumeUpload shape as a tailored resume, just faithful to
    the resume as-is rather than tailored to a job. Raises the same
    HTTPException (missing default key) / LlmError as every other resume LLM
    endpoint on failure; callers that want best-effort (e.g. upload) must
    catch those themselves.
    """
    key = get_users_default_llm_key(db, current_user.id)
    result = extract_resume_structure_with_llm(
        resume.parsed_text,
        provider=key.provider,
        model=key.model,
        api_key=key.get_plaintext_key(),
        base_url=key.base_url,
    )
    content = TailoredResumeUpload(
        summary=result.summary,
        sections=[ResumeSectionContent(**section) for section in result.sections],
        contact=ContactInfo(**result.contact) if result.contact else None,
    )
    resume.structured_content = content.model_dump()
    return resume.structured_content


@router.post("", response_model=ResumeRead, status_code=status.HTTP_201_CREATED)
def upload_resume(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Resume:
    if file.content_type not in SUPPORTED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported file type: {file.content_type!r}. Upload a PDF or DOCX.",
        )

    data = file.file.read()
    if len(data) > _MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="File too large (max 5MB).")

    try:
        parsed_text = extract_text(data, file.content_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    if not parsed_text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Could not extract any text from this file.",
        )

    # A user's very first resume becomes their main one automatically —
    # every subsequent upload needs an explicit PATCH is_main=true.
    is_first = (
        db.scalar(select(func.count()).select_from(Resume).where(Resume.user_id == current_user.id)) == 0
    )

    safe_filename = (file.filename or "resume").replace("/", "_")
    storage_key = f"resumes/{current_user.id}/{uuid.uuid4()}-{safe_filename}"
    upload_file(storage_key, data, file.content_type)

    resume = Resume(
        user_id=current_user.id,
        filename=safe_filename,
        content_type=file.content_type,
        storage_key=storage_key,
        parsed_text=parsed_text,
        is_main=is_first,
    )
    db.add(resume)
    db.flush()

    # Best-effort: a brand-new user very plausibly hasn't added an LLM key
    # yet (get_users_default_llm_key 422s in that case), and an upload must
    # never fail just because structuring couldn't happen — the raw file is
    # still fully usable either way. The frontend can retry later via
    # POST /resumes/{id}/structure once a key is in place.
    try:
        _structure_resume(db, current_user, resume)
    except (HTTPException, LlmError) as exc:
        logger.info("Skipped auto-structuring resume %s at upload: %s", resume.id, exc)

    return resume


@router.get("", response_model=list[ResumeRead])
def list_resumes(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[Resume]:
    return db.scalars(
        select(Resume).where(Resume.user_id == current_user.id).order_by(Resume.created_at.desc())
    ).all()


# Registered before /{resume_id} so "main" (a single path segment, like a
# resume_id) isn't swallowed by that route — see the analogous fix on
# GET /jobs/locations in app.api.routes.jobs.
@router.get("/main", response_model=ResumeDetailRead)
def get_main_resume_detail(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Resume:
    return _get_main_resume(db, current_user.id)


@router.patch("/{resume_id}", response_model=ResumeRead)
def update_resume(
    resume_id: uuid.UUID,
    payload: ResumeUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Resume:
    resume = db.scalar(select(Resume).where(Resume.id == resume_id, Resume.user_id == current_user.id))
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found.")

    if payload.is_main:
        _clear_existing_main(db, current_user.id, except_id=resume.id)
        resume.is_main = True
    db.flush()
    return resume


@router.delete("/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resume(
    resume_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> None:
    resume = db.scalar(select(Resume).where(Resume.id == resume_id, Resume.user_id == current_user.id))
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found.")
    delete_file(resume.storage_key)
    db.delete(resume)


@router.post("/{resume_id}/structure", response_model=ResumeDetailRead)
def structure_resume(
    resume_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Resume:
    """(Re)generate this resume's structured_content via the LLM — see
    _structure_resume. Unlike the best-effort attempt on upload, this raises
    the usual 422 if the user has no default LLM key yet, so the frontend
    can surface that directly (e.g. a link to add one) rather than silently
    leaving structured_content null.
    """
    resume = db.scalar(select(Resume).where(Resume.id == resume_id, Resume.user_id == current_user.id))
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found.")

    try:
        _structure_resume(db, current_user, resume)
    except LlmError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"LLM request failed: {exc}") from exc
    db.flush()
    return resume


@router.get("/{resume_id}/download")
def download_resume(
    resume_id: uuid.UUID,
    format: Literal["docx", "pdf"] | None = None,
    disposition: Literal["inline", "attachment"] = "attachment",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    resume = db.scalar(select(Resume).where(Resume.id == resume_id, Resume.user_id == current_user.id))
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found.")

    if format is None:
        data = download_file(resume.storage_key)
        return Response(
            content=data,
            media_type=resume.content_type,
            # "inline" (not "attachment") so a PDF renders in the browser's
            # own viewer when opened in a new tab instead of forcing a save
            # dialog — DOCX has no native in-browser renderer, so browsers
            # fall back to downloading it regardless of this header.
            headers={"Content-Disposition": f'inline; filename="{resume.filename}"'},
        )

    if resume.structured_content is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Resume hasn't been structured yet — POST /resumes/{resume_id}/structure first.",
        )

    contact = resume.structured_content.get("contact")
    data, media_type = _render_tailored_content(
        resume.structured_content,
        _resolve_contact(ContactInfo(**contact) if contact else None, current_user),
        format,
    )
    base_name = (current_user.display_name or resume.filename.rsplit(".", 1)[0]).replace("/", "_")
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'{disposition}; filename="{base_name}-resume.{format}"'},
    )


@router.get("/{resume_id}/score-history", response_model=ResumeScoreHistoryRead)
def get_resume_score_history(
    resume_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ResumeScoreHistoryRead:
    """Every past scoring of this resume (see ResumeScore's own "history
    kept" note), newest first, plus which missing_keywords keep recurring
    across them — a pattern worth actually fixing on the resume, as
    opposed to a one-off gap a single job happened to want. "Recurring"
    means 2+ separate scores, case-insensitively (a keyword is deduped
    within one score's own list first, so a list that happens to repeat a
    word doesn't inflate its count on its own).
    """
    resume = db.scalar(select(Resume).where(Resume.id == resume_id, Resume.user_id == current_user.id))
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found.")

    scores = db.scalars(
        select(ResumeScore)
        .where(ResumeScore.resume_id == resume_id)
        .order_by(ResumeScore.created_at.desc())
        .options(selectinload(ResumeScore.job_posting))
    ).all()

    entries = [
        ResumeScoreHistoryEntryRead(
            id=score.id,
            job_posting_id=score.job_posting_id,
            url_id=score.job_posting.url_id,
            job_title=score.job_posting.title,
            company_name=score.job_posting.company_name,
            overall_score=score.overall_score,
            missing_keywords=score.missing_keywords,
            created_at=score.created_at,
        )
        for score in scores
    ]

    keyword_counts: Counter[str] = Counter()
    display_form: dict[str, str] = {}
    for score in scores:
        seen_in_this_score: set[str] = set()
        for keyword in score.missing_keywords:
            cleaned = (keyword or "").strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen_in_this_score:
                continue
            seen_in_this_score.add(key)
            keyword_counts[key] += 1
            display_form.setdefault(key, cleaned)
    recurring = [
        RecurringMissingKeywordRead(keyword=display_form[key], count=count)
        for key, count in keyword_counts.most_common()
        if count >= 2
    ][:15]

    return ResumeScoreHistoryRead(entries=entries, recurring_missing_keywords=recurring)


@router.get("/missing-keywords", response_model=MissingSkillsSummaryRead)
def get_missing_keywords_summary(
    resume_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MissingSkillsSummaryRead:
    """The richer analogue of get_resume_score_history's
    recurring_missing_keywords above, for one resume (main resume by
    default, same _resolve_resume convention as every other resume
    endpoint) — attaches the jobs that asked for each skill so a recurring
    gap is easy to trace back to what to add and why. Scoped per-resume
    rather than across all of a user's resumes since scoring itself is
    always done against one selected resume — a candidate running several
    resumes for different tracks would otherwise get gaps blended together
    from roles that have nothing to do with each other.
    """
    resume = _resolve_resume(db, current_user.id, resume_id)
    scores = db.scalars(
        select(ResumeScore)
        .where(ResumeScore.resume_id == resume.id)
        .options(selectinload(ResumeScore.job_posting))
    ).all()

    # Keyed by job_posting_id (not a running count) so rescoring the same
    # job repeatedly doesn't inflate a keyword's job count — mirrors the
    # within-score dedup above, extended to within-job.
    keyword_jobs: dict[str, dict[uuid.UUID, MissingSkillJobRef]] = defaultdict(dict)
    display_form: dict[str, str] = {}
    for score in scores:
        seen_in_this_score: set[str] = set()
        for keyword in score.missing_keywords:
            cleaned = (keyword or "").strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen_in_this_score:
                continue
            seen_in_this_score.add(key)
            display_form.setdefault(key, cleaned)
            keyword_jobs[key][score.job_posting_id] = MissingSkillJobRef(
                job_posting_id=score.job_posting_id,
                url_id=score.job_posting.url_id,
                job_title=score.job_posting.title,
                company_name=score.job_posting.company_name,
            )

    entries = sorted(
        (
            MissingSkillSummaryEntry(keyword=display_form[key], count=len(jobs), jobs=list(jobs.values()))
            for key, jobs in keyword_jobs.items()
            if len(jobs) >= 2
        ),
        key=lambda e: (-e.count, e.keyword.lower()),
    )[:20]
    return MissingSkillsSummaryRead(entries=entries)


_EXPERIENCE_HEADING_KEYWORDS = ("experience", "employment", "work history", "career")


def _roles_from_structured_content(structured_content: dict) -> list[str] | None:
    """Best-effort: pull role labels straight out of an already-structured
    resume's work-history section(s) instead of firing a second LLM call
    (see extract_resume_roles_with_llm). Returns None — the caller should
    fall back to the LLM — when no section heading can be confidently
    identified as work history, rather than risk mixing in education/
    project entries into the "which job does this belong to" dropdown.
    """
    roles: list[str] = []
    matched_any_section = False
    for section in structured_content.get("sections", []):
        heading = (section.get("heading") or "").lower()
        if not any(keyword in heading for keyword in _EXPERIENCE_HEADING_KEYWORDS):
            continue
        matched_any_section = True
        for entry in section.get("entries", []):
            title = entry.get("title", "")
            subtitle = entry.get("subtitle")
            roles.append(f"{title} — {subtitle}" if subtitle else title)
    return roles if matched_any_section else None


@router.get("/{resume_id}/roles", response_model=ResumeRolesRead)
def get_resume_roles(
    resume_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ResumeRolesRead:
    """Labels for this resume's own work-history entries, for the "which job
    does this belong to" dropdown on a ResumeSkillAddition (see below).
    """
    resume = _resolve_resume(db, current_user.id, resume_id)

    if resume.structured_content is not None:
        roles = _roles_from_structured_content(resume.structured_content)
        if roles is not None:
            return ResumeRolesRead(roles=roles)

    key = get_users_default_llm_key(db, current_user.id)

    try:
        result = extract_resume_roles_with_llm(
            resume.parsed_text,
            provider=key.provider,
            model=key.model,
            api_key=key.get_plaintext_key(),
            base_url=key.base_url,
        )
    except LlmError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"LLM request failed: {exc}") from exc

    return ResumeRolesRead(roles=result.roles)


@router.get("/{resume_id}/skill-additions", response_model=list[ResumeSkillAdditionRead])
def list_resume_skill_additions(
    resume_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[ResumeSkillAddition]:
    resume = _resolve_resume(db, current_user.id, resume_id)
    return list(
        db.scalars(
            select(ResumeSkillAddition)
            .where(ResumeSkillAddition.resume_id == resume.id)
            .order_by(ResumeSkillAddition.created_at)
        ).all()
    )


@router.post(
    "/{resume_id}/skill-additions", response_model=ResumeSkillAdditionRead, status_code=status.HTTP_201_CREATED
)
def upsert_resume_skill_addition(
    resume_id: uuid.UUID,
    payload: ResumeSkillAdditionUpsert,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeSkillAddition:
    """Saved immediately (not just held in frontend state) so working
    through several skills survives a refresh — see
    ResumeSkillAddition's own docstring. One row per (resume_id, keyword);
    calling this again for a keyword already drafted edits that draft
    in place rather than creating a second one.
    """
    resume = _resolve_resume(db, current_user.id, resume_id)
    addition = db.scalar(
        select(ResumeSkillAddition).where(
            ResumeSkillAddition.resume_id == resume.id, ResumeSkillAddition.keyword == payload.keyword
        )
    )
    if addition is None:
        addition = ResumeSkillAddition(resume_id=resume.id, user_id=current_user.id, keyword=payload.keyword)
        db.add(addition)
    addition.target_role = payload.target_role
    addition.explanation = payload.explanation
    db.flush()
    return addition


@router.delete("/{resume_id}/skill-additions/{addition_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resume_skill_addition(
    resume_id: uuid.UUID,
    addition_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    resume = _resolve_resume(db, current_user.id, resume_id)
    addition = db.scalar(
        select(ResumeSkillAddition).where(
            ResumeSkillAddition.id == addition_id, ResumeSkillAddition.resume_id == resume.id
        )
    )
    if addition is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill addition not found.")
    db.delete(addition)
    db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{resume_id}/skill-additions/apply", response_model=ResumeRead, status_code=status.HTTP_201_CREATED)
def apply_resume_skill_additions(
    resume_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Resume:
    """The batch action behind "Add missing skills to resume": turns every
    staged ResumeSkillAddition for this resume into a bullet point and
    produces a brand-new Resume (is_main=False — additive and reversible;
    the candidate reviews and promotes it themselves from "My resume")
    rather than editing the existing one in place. Consumes (deletes) the
    drafts on success so the page returns to a clean slate.
    """
    resume = _resolve_resume(db, current_user.id, resume_id)
    additions = list(
        db.scalars(select(ResumeSkillAddition).where(ResumeSkillAddition.resume_id == resume.id)).all()
    )
    if not additions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Add at least one skill first — POST /resumes/{resume_id}/skill-additions.",
        )
    key = get_users_default_llm_key(db, current_user.id)

    try:
        result = apply_skill_additions_with_llm(
            resume.parsed_text,
            [
                {"keyword": a.keyword, "target_role": a.target_role, "explanation": a.explanation}
                for a in additions
            ],
            provider=key.provider,
            model=key.model,
            api_key=key.get_plaintext_key(),
            base_url=key.base_url,
        )
    except LlmError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"LLM request failed: {exc}") from exc

    content = TailoredResumeUpload(
        summary=result.summary,
        sections=[ResumeSectionContent(**section) for section in result.sections],
        contact=ContactInfo(**result.contact) if result.contact else None,
    )
    content_dict = content.model_dump()
    docx_bytes = render_tailored_resume_docx(
        content.summary, _sections_tuples(content_dict["sections"]), _resolve_contact(content.contact, current_user)
    )
    filename = f"{(current_user.display_name or '').replace('/', '_')}-resume-updated.docx"
    storage_key = f"resumes/{current_user.id}/{uuid.uuid4()}-{filename}"
    upload_file(storage_key, docx_bytes, _TAILORED_CONTENT_TYPE)

    new_resume = Resume(
        user_id=current_user.id,
        filename=filename,
        content_type=_TAILORED_CONTENT_TYPE,
        storage_key=storage_key,
        parsed_text=_tailored_resume_text(content_dict),
        is_main=False,
        # Already have the full structured content right here — save the
        # extra LLM round-trip a POST .../structure call would otherwise
        # need, so this new resume is immediately downloadable as docx/PDF.
        structured_content=content_dict,
    )
    db.add(new_resume)
    for addition in additions:
        db.delete(addition)
    db.flush()
    return new_resume


@router.post("/main/review", response_model=ResumeReviewRead)
def review_main_resume(
    resume_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeReview:
    resume = _resolve_resume(db, current_user.id, resume_id)
    key = get_users_default_llm_key(db, current_user.id)

    try:
        result = review_resume_with_llm(
            resume.parsed_text,
            provider=key.provider,
            model=key.model,
            api_key=key.get_plaintext_key(),
            base_url=key.base_url,
        )
    except LlmError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"LLM request failed: {exc}") from exc

    review = ResumeReview(
        resume_id=resume.id,
        user_id=current_user.id,
        strengths=result.strengths,
        weaknesses=result.weaknesses,
        suggestions=result.suggestions,
        summary=result.summary,
        raw_response=result.raw_response,
    )
    db.add(review)
    db.flush()
    return review


@router.get("/main/review", response_model=ResumeReviewRead)
def get_main_resume_review(
    resume_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeReview:
    resume = _resolve_resume(db, current_user.id, resume_id)
    review = db.scalar(
        select(ResumeReview).where(ResumeReview.resume_id == resume.id).order_by(ResumeReview.created_at.desc())
    )
    if review is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No review yet — POST /resumes/main/review first."
        )
    return review


@router.post("/main/score", response_model=ResumeScoreRead)
def score_main_resume(
    job_posting_id: uuid.UUID,
    resume_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeScore:
    resume = _resolve_resume(db, current_user.id, resume_id)
    posting = _get_job_posting(db, job_posting_id)
    key = get_users_default_llm_key(db, current_user.id)

    try:
        result = quick_score_resume_with_llm(
            resume.parsed_text,
            posting.description or "",
            provider=key.provider,
            model=key.model,
            api_key=key.get_plaintext_key(),
            base_url=key.base_url,
        )
    except LlmError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"LLM request failed: {exc}") from exc

    score = ResumeScore(
        resume_id=resume.id,
        user_id=current_user.id,
        job_posting_id=posting.id,
        overall_score=result.overall_score,
        matched_keywords=result.matched_keywords,
        missing_keywords=result.missing_keywords,
        summary=result.summary,
        overqualification_note=result.overqualification_note,
        raw_response={"score": result.raw_response},
    )
    db.add(score)
    db.flush()
    _stamp_application_pointer(db, current_user.id, posting.id, latest_score_id=score.id)
    return score


@router.post("/main/evaluation", response_model=ResumeScoreRead)
def evaluate_main_resume(
    job_posting_id: uuid.UUID,
    resume_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeScore:
    """Comprehensive, opt-in follow-up to POST /resumes/main/score: fills in
    the latest score's per-category breakdown in place (same row — see
    ResumeScore's docstring), pinned to that score's already-decided
    overall_score so the two calls can never disagree on the number.
    """
    resume = _resolve_resume(db, current_user.id, resume_id)
    posting = _get_job_posting(db, job_posting_id)
    key = get_users_default_llm_key(db, current_user.id)

    score = db.scalar(
        select(ResumeScore)
        .where(ResumeScore.resume_id == resume.id, ResumeScore.job_posting_id == posting.id)
        .order_by(ResumeScore.created_at.desc())
    )
    if score is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No score yet for this job — POST /resumes/main/score first.",
        )

    try:
        result = evaluate_resume_with_llm(
            resume.parsed_text,
            posting.description or "",
            score.overall_score,
            provider=key.provider,
            model=key.model,
            api_key=key.get_plaintext_key(),
            base_url=key.base_url,
        )
    except LlmError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"LLM request failed: {exc}") from exc

    score.category_scores = result.category_scores
    score.raw_response = {**(score.raw_response or {}), "evaluation": result.raw_response}
    db.flush()
    return score


@router.post("/main/score/upload", response_model=ResumeScoreRead, status_code=status.HTTP_201_CREATED)
def upload_main_resume_score(
    job_posting_id: uuid.UUID,
    payload: ResumeScoreUpload,
    resume_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeScore:
    """Store a fit evaluation computed elsewhere (e.g. by an MCP client's own
    LLM — see mcp_server/) as the current score for a job, skipping this
    app's own LLM call. Same storage as the generate endpoint above.
    """
    resume = _resolve_resume(db, current_user.id, resume_id)
    posting = _get_job_posting(db, job_posting_id)

    score = ResumeScore(
        resume_id=resume.id,
        user_id=current_user.id,
        job_posting_id=posting.id,
        overall_score=payload.overall_score,
        matched_keywords=payload.matched_keywords,
        missing_keywords=payload.missing_keywords,
        summary=payload.summary,
        category_scores=[item.model_dump() for item in payload.category_scores],
        overqualification_note=payload.overqualification_note,
        raw_response=None,
    )
    db.add(score)
    db.flush()
    _stamp_application_pointer(db, current_user.id, posting.id, latest_score_id=score.id)
    return score


@router.get("/main/score", response_model=ResumeScoreRead)
def get_main_resume_score(
    job_posting_id: uuid.UUID,
    resume_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeScore:
    resume = _resolve_resume(db, current_user.id, resume_id)
    score = db.scalar(
        select(ResumeScore)
        .where(ResumeScore.resume_id == resume.id, ResumeScore.job_posting_id == job_posting_id)
        .order_by(ResumeScore.created_at.desc())
    )
    if score is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No score yet for this job — POST /resumes/main/score first.",
        )
    return score


def _resolve_contact(contact: ContactInfo | None, current_user: User) -> dict[str, str]:
    """Merge LLM-extracted (or /upload-supplied) contact fields with a
    fallback to the User row's own name/email — so a document is never
    fully contact-less, even if extraction missed something or an /upload
    caller (e.g. an MCP client) didn't send a contact block at all.
    """
    fields = contact.model_dump() if contact else {}
    return {
        "name": (fields.get("name") or current_user.display_name or "").strip(),
        "email": (fields.get("email") or current_user.email or "").strip(),
        "phone": (fields.get("phone") or "").strip(),
        "location": (fields.get("location") or "").strip(),
        "linkedin": (fields.get("linkedin") or "").strip(),
    }


def _store_tailored_resume(
    db: Session,
    current_user: User,
    resume: Resume,
    posting: JobPosting,
    content: TailoredResumeUpload,
    raw_response: dict | None,
) -> TailoredResume:
    docx_bytes = render_tailored_resume_docx(
        content.summary,
        [
            (section.heading, section.bullets, [entry.model_dump() for entry in section.entries])
            for section in content.sections
        ],
        _resolve_contact(content.contact, current_user),
    )
    filename = f"{(current_user.display_name or '').replace('/', '_')}-{(posting.title or '').replace('/', '_')}-resume.docx"
    storage_key = f"tailored/{current_user.id}/{uuid.uuid4()}-{filename}"
    upload_file(storage_key, docx_bytes, _TAILORED_CONTENT_TYPE)

    tailored = TailoredResume(
        resume_id=resume.id,
        user_id=current_user.id,
        job_posting_id=posting.id,
        content=content.model_dump(),
        storage_key=storage_key,
        filename=filename,
        raw_response=raw_response,
    )
    db.add(tailored)
    db.flush()
    _stamp_application_pointer(db, current_user.id, posting.id, latest_tailored_resume_id=tailored.id)
    return tailored


def _latest_resume_score_dict(db: Session, resume_id: uuid.UUID, job_posting_id: uuid.UUID) -> dict | None:
    """The latest ResumeScore for this resume+job, as a plain dict for
    passing into generate_tailored_resume_with_llm — None if the user
    hasn't scored this pairing yet (tailoring still works, just without
    that context).
    """
    score = db.scalar(
        select(ResumeScore)
        .where(ResumeScore.resume_id == resume_id, ResumeScore.job_posting_id == job_posting_id)
        .order_by(ResumeScore.created_at.desc())
    )
    if score is None:
        return None
    return {
        "overall_score": score.overall_score,
        "matched_keywords": score.matched_keywords,
        "missing_keywords": score.missing_keywords,
        "summary": score.summary,
        "category_scores": score.category_scores,
        "overqualification_note": score.overqualification_note,
    }


@router.post("/main/tailored", response_model=TailoredResumeRead, status_code=status.HTTP_201_CREATED)
def generate_main_tailored_resume(
    job_posting_id: uuid.UUID,
    resume_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TailoredResume:
    resume = _resolve_resume(db, current_user.id, resume_id)
    posting = _get_job_posting(db, job_posting_id)
    key = get_users_default_llm_key(db, current_user.id)
    fitness_score = _latest_resume_score_dict(db, resume.id, posting.id)

    try:
        generated = generate_tailored_resume_with_llm(
            resume.parsed_text,
            posting.description or "",
            provider=key.provider,
            model=key.model,
            api_key=key.get_plaintext_key(),
            base_url=key.base_url,
            fitness_score=fitness_score,
        )
    except LlmError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"LLM request failed: {exc}") from exc

    content = TailoredResumeUpload(
        summary=generated.summary,
        sections=[ResumeSectionContent(**section) for section in generated.sections],
        contact=ContactInfo(**generated.contact) if generated.contact else None,
    )
    return _store_tailored_resume(db, current_user, resume, posting, content, generated.raw_response)


@router.post("/main/tailored/upload", response_model=TailoredResumeRead, status_code=status.HTTP_201_CREATED)
def upload_main_tailored_resume(
    job_posting_id: uuid.UUID,
    payload: TailoredResumeUpload,
    resume_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TailoredResume:
    """Store a tailored resume written elsewhere (e.g. by an MCP client's
    own LLM — see mcp_server/) as the current tailored resume for a job,
    skipping this app's own LLM call. Same content -> .docx pipeline as
    the generate endpoint above.
    """
    resume = _resolve_resume(db, current_user.id, resume_id)
    posting = _get_job_posting(db, job_posting_id)
    return _store_tailored_resume(db, current_user, resume, posting, payload, None)


@router.get("/main/tailored", response_model=TailoredResumeRead)
def get_main_tailored_resume(
    job_posting_id: uuid.UUID,
    resume_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TailoredResume:
    resume = _resolve_resume(db, current_user.id, resume_id)
    tailored = db.scalar(
        select(TailoredResume)
        .where(TailoredResume.resume_id == resume.id, TailoredResume.job_posting_id == job_posting_id)
        .order_by(TailoredResume.created_at.desc())
    )
    if tailored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No tailored resume yet for this job — POST /resumes/main/tailored first.",
        )
    return tailored


@router.post("/tailored/{tailored_id}/score", response_model=TailoredResumeScoreRead)
def score_tailored_resume(
    tailored_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TailoredResumeScore:
    tailored = _get_owned_tailored_resume(db, current_user.id, tailored_id)
    posting = _get_job_posting(db, tailored.job_posting_id)
    key = get_users_default_llm_key(db, current_user.id)

    try:
        result = quick_score_resume_with_llm(
            _tailored_resume_text(tailored.content),
            posting.description or "",
            provider=key.provider,
            model=key.model,
            api_key=key.get_plaintext_key(),
            base_url=key.base_url,
        )
    except LlmError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"LLM request failed: {exc}") from exc

    score = TailoredResumeScore(
        tailored_resume_id=tailored.id,
        user_id=current_user.id,
        job_posting_id=posting.id,
        overall_score=result.overall_score,
        matched_keywords=result.matched_keywords,
        missing_keywords=result.missing_keywords,
        summary=result.summary,
        overqualification_note=result.overqualification_note,
        raw_response={"score": result.raw_response},
    )
    db.add(score)
    db.flush()
    _stamp_application_pointer(db, current_user.id, posting.id, latest_tailored_resume_score_id=score.id)
    return score


@router.post("/tailored/{tailored_id}/evaluation", response_model=TailoredResumeScoreRead)
def evaluate_tailored_resume(
    tailored_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TailoredResumeScore:
    """Comprehensive, opt-in follow-up to POST /resumes/tailored/{id}/score
    — the tailored-resume analogue of evaluate_main_resume above.
    """
    tailored = _get_owned_tailored_resume(db, current_user.id, tailored_id)
    posting = _get_job_posting(db, tailored.job_posting_id)
    key = get_users_default_llm_key(db, current_user.id)

    score = db.scalar(
        select(TailoredResumeScore)
        .where(TailoredResumeScore.tailored_resume_id == tailored.id)
        .order_by(TailoredResumeScore.created_at.desc())
    )
    if score is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No score yet for this tailored resume — POST /resumes/tailored/{id}/score first.",
        )

    try:
        result = evaluate_resume_with_llm(
            _tailored_resume_text(tailored.content),
            posting.description or "",
            score.overall_score,
            provider=key.provider,
            model=key.model,
            api_key=key.get_plaintext_key(),
            base_url=key.base_url,
        )
    except LlmError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"LLM request failed: {exc}") from exc

    score.category_scores = result.category_scores
    score.raw_response = {**(score.raw_response or {}), "evaluation": result.raw_response}
    db.flush()
    return score


@router.post(
    "/tailored/{tailored_id}/score/upload", response_model=TailoredResumeScoreRead, status_code=status.HTTP_201_CREATED
)
def upload_tailored_resume_score(
    tailored_id: uuid.UUID,
    payload: TailoredResumeScoreUpload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TailoredResumeScore:
    """Store a fit evaluation of a tailored resume computed elsewhere (e.g.
    by an MCP client's own LLM — see mcp_server/) as the current score for
    that tailored resume, skipping this app's own LLM call. Same storage as
    the generate endpoint above.
    """
    tailored = _get_owned_tailored_resume(db, current_user.id, tailored_id)
    posting = _get_job_posting(db, tailored.job_posting_id)

    score = TailoredResumeScore(
        tailored_resume_id=tailored.id,
        user_id=current_user.id,
        job_posting_id=posting.id,
        overall_score=payload.overall_score,
        matched_keywords=payload.matched_keywords,
        missing_keywords=payload.missing_keywords,
        summary=payload.summary,
        category_scores=[item.model_dump() for item in payload.category_scores],
        overqualification_note=payload.overqualification_note,
        raw_response=None,
    )
    db.add(score)
    db.flush()
    _stamp_application_pointer(db, current_user.id, posting.id, latest_tailored_resume_score_id=score.id)
    return score


@router.get("/tailored/{tailored_id}/score", response_model=TailoredResumeScoreRead)
def get_tailored_resume_score(
    tailored_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TailoredResumeScore:
    tailored = _get_owned_tailored_resume(db, current_user.id, tailored_id)
    score = db.scalar(
        select(TailoredResumeScore)
        .where(TailoredResumeScore.tailored_resume_id == tailored.id)
        .order_by(TailoredResumeScore.created_at.desc())
    )
    if score is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No score yet for this tailored resume — POST /resumes/tailored/{id}/score first.",
        )
    return score


@router.get("/tailored/{tailored_id}/download")
def download_tailored_resume(
    tailored_id: uuid.UUID,
    format: Literal["docx", "pdf"] = "docx",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    tailored = _get_owned_tailored_resume(db, current_user.id, tailored_id)

    if format == "docx":
        # Unchanged from before format= existed — the same pre-rendered
        # bytes already sitting in storage, no on-the-fly rendering.
        data = download_file(tailored.storage_key)
        return Response(
            content=data,
            media_type=_TAILORED_CONTENT_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{tailored.filename}"'},
        )

    contact = tailored.content.get("contact")
    data, media_type = _render_tailored_content(
        tailored.content, _resolve_contact(ContactInfo(**contact) if contact else None, current_user), format
    )
    pdf_filename = f"{tailored.filename.rsplit('.', 1)[0]}.pdf"
    return Response(
        content=data, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{pdf_filename}"'}
    )


def _store_cover_letter(
    db: Session,
    current_user: User,
    resume: Resume,
    posting: JobPosting,
    content: CoverLetterUpload,
    raw_response: dict | None,
) -> CoverLetter:
    docx_bytes = render_cover_letter_docx(
        content.greeting, content.body_paragraphs, content.closing, _resolve_contact(content.contact, current_user)
    )
    filename = f"{(current_user.display_name or '').replace('/', '_')}-{(posting.title or '').replace('/', '_')}-cover-letter.docx"
    storage_key = f"cover-letters/{current_user.id}/{uuid.uuid4()}-{filename}"
    upload_file(storage_key, docx_bytes, _TAILORED_CONTENT_TYPE)

    cover_letter = CoverLetter(
        resume_id=resume.id,
        user_id=current_user.id,
        job_posting_id=posting.id,
        content=content.model_dump(),
        storage_key=storage_key,
        filename=filename,
        raw_response=raw_response,
    )
    db.add(cover_letter)
    db.flush()
    _stamp_application_pointer(db, current_user.id, posting.id, latest_cover_letter_id=cover_letter.id)
    return cover_letter


@router.post("/main/cover-letter", response_model=CoverLetterRead, status_code=status.HTTP_201_CREATED)
def generate_main_cover_letter(
    job_posting_id: uuid.UUID,
    resume_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CoverLetter:
    resume = _resolve_resume(db, current_user.id, resume_id)
    posting = _get_job_posting(db, job_posting_id)
    key = get_users_default_llm_key(db, current_user.id)

    try:
        generated = generate_cover_letter_with_llm(
            resume.parsed_text,
            posting.description or "",
            provider=key.provider,
            model=key.model,
            api_key=key.get_plaintext_key(),
            base_url=key.base_url,
        )
    except LlmError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"LLM request failed: {exc}") from exc

    content = CoverLetterUpload(
        greeting=generated.greeting,
        body_paragraphs=generated.body_paragraphs,
        closing=generated.closing,
        contact=ContactInfo(**generated.contact) if generated.contact else None,
    )
    return _store_cover_letter(db, current_user, resume, posting, content, generated.raw_response)


@router.post("/main/cover-letter/upload", response_model=CoverLetterRead, status_code=status.HTTP_201_CREATED)
def upload_main_cover_letter(
    job_posting_id: uuid.UUID,
    payload: CoverLetterUpload,
    resume_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CoverLetter:
    """Store a cover letter written elsewhere (e.g. by an MCP client's own
    LLM — see mcp_server/) as the current cover letter for a job, skipping
    this app's own LLM call. Same content -> .docx pipeline as the generate
    endpoint above.
    """
    resume = _resolve_resume(db, current_user.id, resume_id)
    posting = _get_job_posting(db, job_posting_id)
    return _store_cover_letter(db, current_user, resume, posting, payload, None)


@router.get("/main/cover-letter", response_model=CoverLetterRead)
def get_main_cover_letter(
    job_posting_id: uuid.UUID,
    resume_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CoverLetter:
    resume = _resolve_resume(db, current_user.id, resume_id)
    cover_letter = db.scalar(
        select(CoverLetter)
        .where(CoverLetter.resume_id == resume.id, CoverLetter.job_posting_id == job_posting_id)
        .order_by(CoverLetter.created_at.desc())
    )
    if cover_letter is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No cover letter yet for this job — POST /resumes/main/cover-letter first.",
        )
    return cover_letter


@router.get("/cover-letter/{cover_letter_id}/download")
def download_cover_letter(
    cover_letter_id: uuid.UUID,
    format: Literal["docx", "pdf"] = "docx",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    cover_letter = db.scalar(
        select(CoverLetter).where(CoverLetter.id == cover_letter_id, CoverLetter.user_id == current_user.id)
    )
    if cover_letter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cover letter not found.")

    if format == "docx":
        # Unchanged from before format= existed — the same pre-rendered
        # bytes already sitting in storage, no on-the-fly rendering.
        data = download_file(cover_letter.storage_key)
        return Response(
            content=data,
            media_type=_TAILORED_CONTENT_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{cover_letter.filename}"'},
        )

    contact = cover_letter.content.get("contact")
    data, media_type = _render_cover_letter_content(
        cover_letter.content, _resolve_contact(ContactInfo(**contact) if contact else None, current_user), format
    )
    pdf_filename = f"{cover_letter.filename.rsplit('.', 1)[0]}.pdf"
    return Response(
        content=data, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{pdf_filename}"'}
    )


@router.post("/main/interview-prep", response_model=InterviewPrepRead, status_code=status.HTTP_201_CREATED)
def generate_main_interview_prep(
    job_posting_id: uuid.UUID,
    resume_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InterviewPrep:
    resume = _resolve_resume(db, current_user.id, resume_id)
    posting = _get_job_posting(db, job_posting_id)
    key = get_users_default_llm_key(db, current_user.id)

    try:
        generated = generate_interview_prep_with_llm(
            resume.parsed_text,
            posting.description or "",
            provider=key.provider,
            model=key.model,
            api_key=key.get_plaintext_key(),
            base_url=key.base_url,
        )
    except LlmError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"LLM request failed: {exc}") from exc

    prep = InterviewPrep(
        resume_id=resume.id,
        user_id=current_user.id,
        job_posting_id=posting.id,
        content={
            "likely_questions": generated.likely_questions,
            "talking_points": generated.talking_points,
            "questions_to_ask": generated.questions_to_ask,
        },
        raw_response=generated.raw_response,
    )
    db.add(prep)
    db.flush()
    return prep


@router.get("/main/interview-prep", response_model=InterviewPrepRead)
def get_main_interview_prep(
    job_posting_id: uuid.UUID,
    resume_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InterviewPrep:
    resume = _resolve_resume(db, current_user.id, resume_id)
    prep = db.scalar(
        select(InterviewPrep)
        .where(InterviewPrep.resume_id == resume.id, InterviewPrep.job_posting_id == job_posting_id)
        .order_by(InterviewPrep.created_at.desc())
    )
    if prep is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No interview prep yet for this job — POST /resumes/main/interview-prep first.",
        )
    return prep
