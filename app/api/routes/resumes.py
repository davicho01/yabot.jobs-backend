import uuid
from collections import Counter

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
    TailoredResume,
    TailoredResumeScore,
)
from app.models.user import User
from app.schemas.resume import (
    ContactInfo,
    CoverLetterRead,
    CoverLetterUpload,
    InterviewPrepRead,
    RecurringMissingKeywordRead,
    ResumeDetailRead,
    ResumeRead,
    ResumeReviewRead,
    ResumeScoreHistoryEntryRead,
    ResumeScoreHistoryRead,
    ResumeScoreRead,
    ResumeScoreUpload,
    ResumeSectionContent,
    ResumeUpdate,
    TailoredResumeRead,
    TailoredResumeScoreRead,
    TailoredResumeScoreUpload,
    TailoredResumeUpload,
)
from app.services.llm_client import LlmError
from app.services.resume_llm import (
    generate_cover_letter_with_llm,
    generate_interview_prep_with_llm,
    generate_tailored_resume_with_llm,
    get_users_default_llm_key,
    review_resume_with_llm,
    score_resume_with_llm,
)
from app.services.resume_parser import SUPPORTED_CONTENT_TYPES, extract_text
from app.services.resume_renderer import render_cover_letter_docx, render_tailored_resume_docx
from app.services.resume_storage import delete_file, download_file, upload_file

router = APIRouter(prefix="/resumes", tags=["resumes"])

_MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
_TAILORED_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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
    lines = [content.get("summary", "")]
    for section in content.get("sections", []):
        lines.append(section.get("heading", ""))
        lines.extend(f"- {b}" for b in section.get("bullets", []))
    return "\n".join(lines)


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


@router.get("/{resume_id}/download")
def download_resume(
    resume_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Response:
    resume = db.scalar(select(Resume).where(Resume.id == resume_id, Resume.user_id == current_user.id))
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found.")

    data = download_file(resume.storage_key)
    return Response(
        content=data,
        media_type=resume.content_type,
        # "inline" (not "attachment") so a PDF renders in the browser's own
        # viewer when opened in a new tab instead of forcing a save dialog —
        # DOCX has no native in-browser renderer, so browsers fall back to
        # downloading it regardless of this header.
        headers={"Content-Disposition": f'inline; filename="{resume.filename}"'},
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
        result = score_resume_with_llm(
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
        category_scores=result.category_scores,
        overqualification_note=result.overqualification_note,
        raw_response=result.raw_response,
    )
    db.add(score)
    db.flush()
    _stamp_application_pointer(db, current_user.id, posting.id, latest_score_id=score.id)
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
        result = score_resume_with_llm(
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
        category_scores=result.category_scores,
        overqualification_note=result.overqualification_note,
        raw_response=result.raw_response,
    )
    db.add(score)
    db.flush()
    _stamp_application_pointer(db, current_user.id, posting.id, latest_tailored_resume_score_id=score.id)
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
    tailored_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Response:
    tailored = _get_owned_tailored_resume(db, current_user.id, tailored_id)

    data = download_file(tailored.storage_key)
    return Response(
        content=data,
        media_type=_TAILORED_CONTENT_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{tailored.filename}"'},
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
    cover_letter_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Response:
    cover_letter = db.scalar(
        select(CoverLetter).where(CoverLetter.id == cover_letter_id, CoverLetter.user_id == current_user.id)
    )
    if cover_letter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cover letter not found.")

    data = download_file(cover_letter.storage_key)
    return Response(
        content=data,
        media_type=_TAILORED_CONTENT_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{cover_letter.filename}"'},
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
