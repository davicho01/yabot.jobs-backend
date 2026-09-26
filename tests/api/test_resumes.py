import io
import itertools
import uuid
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import Headers

import app.models as m
from app.api.routes import resumes as resumes_routes
from app.api.routes.resumes import (
    _resolve_resume,
    _roles_from_structured_content,
    _tailored_resume_text,
    apply_resume_skill_additions,
    delete_resume,
    delete_resume_skill_addition,
    download_cover_letter,
    download_resume,
    download_tailored_resume,
    get_main_interview_prep,
    get_missing_keywords_summary,
    get_resume_roles,
    get_resume_score_history,
    get_resume_versions,
    list_resume_skill_additions,
    list_resumes,
    restore_resume_version,
    structure_resume,
    update_resume,
    upload_resume,
    upsert_resume_skill_addition,
)
from app.db.base import Base
from app.models.resume import CoverLetter, InterviewPrep
from app.schemas.resume import ResumeSkillAdditionUpsert, ResumeUpdate
from app.services.llm_client import LlmError
from app.services.resume_llm import TailoredResumeContent

_counter = itertools.count()


@pytest.fixture
def db() -> Session:
    # Local db fixture (not tests/api/conftest.py's, which only has
    # SavedSearch) — _resolve_resume/get_resume_score_history need Resume,
    # ResumeScore and (for the score history's job title/company) JobPosting;
    # get_main_interview_prep also needs InterviewPrep; the skill-addition
    # routes need ResumeSkillAddition.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            m.Resume.__table__,
            m.ResumeScore.__table__,
            m.ResumeSkillAddition.__table__,
            m.JobPostingUrl.__table__,
            m.JobPosting.__table__,
            InterviewPrep.__table__,
            m.TailoredResume.__table__,
            CoverLetter.__table__,
        ],
    )
    with engine.begin() as conn:
        # Resume's one-main-per-user index is partial in Postgres
        # (postgresql_where=text("is_main")) — SQLite doesn't understand
        # that dialect-specific clause and silently drops it, so create_all
        # gives SQLite a *full* unique index on user_id instead, blocking a
        # user from having more than one resume at all. Drop it: these
        # tests need several resumes per user, and the one-main invariant
        # it enforces isn't what's under test here.
        conn.execute(text("DROP INDEX IF EXISTS uq_resumes_one_main_per_user"))
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


def _make_resume(
    db,
    user_id: uuid.UUID,
    *,
    is_main: bool = False,
    root_resume_id: uuid.UUID | None = None,
    version_number: int = 1,
) -> m.Resume:
    n = next(_counter)
    resume_id = uuid.uuid4()
    resume = m.Resume(
        id=resume_id,
        user_id=user_id,
        filename=f"resume-{n}.pdf",
        content_type="application/pdf",
        storage_key=f"resumes/{user_id}/{n}",
        parsed_text=f"Resume text {n}",
        is_main=is_main,
        root_resume_id=root_resume_id if root_resume_id is not None else resume_id,
        version_number=version_number,
    )
    db.add(resume)
    db.commit()
    return resume


def _make_job_posting(db, *, title: str = "Backend Engineer", company_name: str = "Acme") -> m.JobPosting:
    n = next(_counter)
    url_row = m.JobPostingUrl(
        url=f"https://example.com/jobs/score-history-{n}",
        normalized_url=f"https://example.com/jobs/score-history-{n}",
        url_hash=f"score-history-hash-{n}",
        domain="example.com",
    )
    db.add(url_row)
    db.flush()
    posting = m.JobPosting(url_id=url_row.id, title=title, company_name=company_name)
    db.add(posting)
    db.commit()
    return posting


def _make_score(
    db,
    resume: m.Resume,
    posting: m.JobPosting,
    *,
    overall_score: int = 70,
    missing_keywords: list[str] | None = None,
) -> m.ResumeScore:
    score = m.ResumeScore(
        resume_id=resume.id,
        user_id=resume.user_id,
        job_posting_id=posting.id,
        overall_score=overall_score,
        matched_keywords=[],
        missing_keywords=missing_keywords or [],
        summary="",
    )
    db.add(score)
    db.commit()
    return score


def _fake_key() -> Mock:
    key = Mock()
    key.provider = "openai"
    key.model = "test-model"
    key.base_url = None
    key.get_plaintext_key.return_value = "test-key"
    return key


def _make_tailored_resume(db, user_id: uuid.UUID, resume: m.Resume, posting: m.JobPosting, *, content=None) -> m.TailoredResume:
    tailored = m.TailoredResume(
        resume_id=resume.id,
        user_id=user_id,
        job_posting_id=posting.id,
        content=content or {"summary": "Backend engineer.", "sections": [], "contact": None},
        storage_key=f"tailored/{user_id}/{next(_counter)}",
        filename="Jane-Doe-Backend-Engineer-resume.docx",
    )
    db.add(tailored)
    db.commit()
    return tailored


def _make_cover_letter(db, user_id: uuid.UUID, resume: m.Resume, posting: m.JobPosting, *, content=None) -> CoverLetter:
    cover_letter = CoverLetter(
        resume_id=resume.id,
        user_id=user_id,
        job_posting_id=posting.id,
        content=content or {"greeting": "Dear Hiring Manager,", "body_paragraphs": ["I am interested."], "closing": "Sincerely,"},
        storage_key=f"cover-letters/{user_id}/{next(_counter)}",
        filename="Jane-Doe-Backend-Engineer-cover-letter.docx",
    )
    db.add(cover_letter)
    db.commit()
    return cover_letter


def test_resolve_resume_with_no_id_falls_back_to_the_main_resume(db):
    user_id = uuid.uuid4()
    main = _make_resume(db, user_id, is_main=True)
    _make_resume(db, user_id, is_main=False)  # a second resume, not main

    resolved = _resolve_resume(db, user_id, None)

    assert resolved.id == main.id


def test_resolve_resume_with_an_id_picks_that_resume_even_when_not_main(db):
    user_id = uuid.uuid4()
    _make_resume(db, user_id, is_main=True)
    other = _make_resume(db, user_id, is_main=False)

    resolved = _resolve_resume(db, user_id, other.id)

    assert resolved.id == other.id


def test_resolve_resume_404s_for_a_resume_id_owned_by_someone_else(db):
    owner = uuid.uuid4()
    someone_else = uuid.uuid4()
    theirs = _make_resume(db, owner, is_main=True)

    with pytest.raises(HTTPException) as exc_info:
        _resolve_resume(db, someone_else, theirs.id)
    assert exc_info.value.status_code == 404


def test_resolve_resume_404s_for_an_unknown_resume_id(db):
    with pytest.raises(HTTPException) as exc_info:
        _resolve_resume(db, uuid.uuid4(), uuid.uuid4())
    assert exc_info.value.status_code == 404


def test_resolve_resume_with_no_id_and_no_main_resume_gives_the_original_422(db):
    # Unchanged from before resume_id existed — a user with no main resume
    # set still gets this specific, actionable error, not a generic 404.
    with pytest.raises(HTTPException) as exc_info:
        _resolve_resume(db, uuid.uuid4(), None)
    assert exc_info.value.status_code == 422


# ------------------------------------------------------- resume score history


def test_get_resume_score_history_404s_for_an_unknown_resume(db):
    with pytest.raises(HTTPException) as exc_info:
        get_resume_score_history(uuid.uuid4(), current_user=m.User(id=uuid.uuid4()), db=db)
    assert exc_info.value.status_code == 404


def test_get_resume_score_history_404s_for_a_resume_owned_by_someone_else(db):
    owner_id = uuid.uuid4()
    resume = _make_resume(db, owner_id, is_main=True)

    with pytest.raises(HTTPException) as exc_info:
        get_resume_score_history(resume.id, current_user=m.User(id=uuid.uuid4()), db=db)
    assert exc_info.value.status_code == 404


def test_get_resume_score_history_lists_entries_newest_first_with_job_labels(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    older_job = _make_job_posting(db, title="Backend Engineer", company_name="Acme")
    newer_job = _make_job_posting(db, title="Platform Engineer", company_name="Globex")
    older = _make_score(db, resume, older_job, overall_score=60)
    older.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer = _make_score(db, resume, newer_job, overall_score=80)
    newer.created_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    db.commit()

    result = get_resume_score_history(resume.id, current_user=m.User(id=user_id), db=db)

    assert [e.id for e in result.entries] == [newer.id, older.id]
    assert result.entries[0].job_title == "Platform Engineer"
    assert result.entries[0].company_name == "Globex"
    assert result.entries[0].overall_score == 80
    # What a history row links out to (the ApplyPage route is keyed on
    # this, not job_posting_id) — the posting's own url_id, not its id.
    assert result.entries[0].url_id == newer_job.url_id
    assert result.entries[0].url_id != result.entries[0].job_posting_id


def test_get_resume_score_history_finds_keywords_recurring_across_scores(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    _make_score(db, resume, _make_job_posting(db), missing_keywords=["SQL", "Docker"])
    _make_score(db, resume, _make_job_posting(db), missing_keywords=["sql", "Kubernetes"])
    _make_score(db, resume, _make_job_posting(db), missing_keywords=["Docker"])

    result = get_resume_score_history(resume.id, current_user=m.User(id=user_id), db=db)

    recurring = {k.keyword: k.count for k in result.recurring_missing_keywords}
    # "SQL"/"sql" recur (case-insensitively) across 2 scores; "Docker" across
    # 2; "Kubernetes" only showed up once and isn't a pattern, so it's out.
    assert recurring == {"SQL": 2, "Docker": 2}


def test_get_resume_score_history_does_not_double_count_a_repeat_within_one_score(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    # A single score listing the same keyword twice (defensive — the LLM
    # shouldn't normally do this) must still only count as one occurrence,
    # not manufacture a false "recurring" pattern on its own.
    _make_score(db, resume, _make_job_posting(db), missing_keywords=["SQL", "SQL"])

    result = get_resume_score_history(resume.id, current_user=m.User(id=user_id), db=db)

    assert result.recurring_missing_keywords == []


# ---------------------------------------------------- missing-keywords summary


def test_missing_keywords_summary_aggregates_across_all_of_the_users_resumes(db):
    # A gap flagged while scoring one resume is just as real a gap on any
    # other — the summary is global per-user, not scoped to one resume (the
    # candidate picks which resume actually gets the fix separately, via
    # the resume picker / apply_resume_skill_additions).
    user_id = uuid.uuid4()
    main = _make_resume(db, user_id, is_main=True)
    other = _make_resume(db, user_id, is_main=False)
    _make_score(db, main, _make_job_posting(db), missing_keywords=["SQL", "Docker"])
    _make_score(db, main, _make_job_posting(db), missing_keywords=["sql"])
    _make_score(db, other, _make_job_posting(db), missing_keywords=["Kubernetes"])
    _make_score(db, other, _make_job_posting(db), missing_keywords=["Kubernetes"])

    result = get_missing_keywords_summary(current_user=m.User(id=user_id), db=db)

    by_keyword = {e.keyword: e.count for e in result.entries}
    assert by_keyword == {"SQL": 2, "Kubernetes": 2}


def test_missing_keywords_summary_does_not_double_count_a_repeat_within_one_score(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    _make_score(db, resume, _make_job_posting(db), missing_keywords=["SQL", "SQL"])

    result = get_missing_keywords_summary(current_user=m.User(id=user_id), db=db)

    assert result.entries == []


def test_missing_keywords_summary_does_not_inflate_count_when_the_same_job_is_rescored(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    job = _make_job_posting(db, title="Backend Engineer", company_name="Acme")
    other_job = _make_job_posting(db, title="Platform Engineer", company_name="Globex")
    _make_score(db, resume, job, missing_keywords=["SQL"])
    _make_score(db, resume, job, missing_keywords=["SQL"])  # rescored — same job
    _make_score(db, resume, other_job, missing_keywords=["SQL"])

    result = get_missing_keywords_summary(current_user=m.User(id=user_id), db=db)

    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.keyword == "SQL"
    assert entry.count == 2  # 2 distinct jobs, not 3 scores
    assert {j.job_posting_id for j in entry.jobs} == {job.id, other_job.id}


def test_missing_keywords_summary_includes_job_refs_for_linking(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    job_a = _make_job_posting(db, title="Backend Engineer", company_name="Acme")
    job_b = _make_job_posting(db, title="Platform Engineer", company_name="Globex")
    _make_score(db, resume, job_a, missing_keywords=["Kubernetes"])
    _make_score(db, resume, job_b, missing_keywords=["Kubernetes"])

    result = get_missing_keywords_summary(current_user=m.User(id=user_id), db=db)

    assert len(result.entries) == 1
    jobs_by_title = {j.job_title: j for j in result.entries[0].jobs}
    assert jobs_by_title["Backend Engineer"].company_name == "Acme"
    assert jobs_by_title["Backend Engineer"].url_id == job_a.url_id
    assert jobs_by_title["Platform Engineer"].company_name == "Globex"


def test_missing_keywords_summary_never_leaks_another_users_scores(db):
    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    other_resume = _make_resume(db, other_user_id, is_main=True)
    _make_score(db, other_resume, _make_job_posting(db), missing_keywords=["SQL"])
    _make_score(db, other_resume, _make_job_posting(db), missing_keywords=["SQL"])
    _make_score(db, resume, _make_job_posting(db), missing_keywords=["Docker"])

    result = get_missing_keywords_summary(current_user=m.User(id=user_id), db=db)

    assert result.entries == []  # this user only has one job scored, no recurrence


# ----------------------------------------------------------- skill additions


def _upsert(db, resume_id, user_id, *, keyword="Kubernetes", target_role="General / Skills", explanation="Used it."):
    return upsert_resume_skill_addition(
        resume_id,
        ResumeSkillAdditionUpsert(keyword=keyword, target_role=target_role, explanation=explanation),
        current_user=m.User(id=user_id),
        db=db,
    )


def test_upsert_skill_addition_creates_a_draft(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)

    addition = _upsert(db, resume.id, user_id, keyword="Kubernetes", target_role="DevOps Engineer — Acme")

    assert addition.resume_id == resume.id
    assert addition.keyword == "Kubernetes"
    assert addition.target_role == "DevOps Engineer — Acme"


def test_upsert_skill_addition_edits_the_existing_draft_for_the_same_keyword(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    first = _upsert(db, resume.id, user_id, keyword="SQL", explanation="Wrote reporting queries.")

    second = _upsert(db, resume.id, user_id, keyword="SQL", explanation="Owned the analytics schema.")

    assert second.id == first.id  # same row, not a duplicate
    all_drafts = list_resume_skill_additions(resume.id, current_user=m.User(id=user_id), db=db)
    assert len(all_drafts) == 1
    assert all_drafts[0].explanation == "Owned the analytics schema."


def test_upsert_skill_addition_404s_for_a_resume_owned_by_someone_else(db):
    owner_id = uuid.uuid4()
    resume = _make_resume(db, owner_id, is_main=True)

    with pytest.raises(HTTPException) as exc_info:
        _upsert(db, resume.id, uuid.uuid4())
    assert exc_info.value.status_code == 404


def test_list_skill_additions_only_returns_this_resumes_drafts(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    other_resume = _make_resume(db, user_id, is_main=False)
    _upsert(db, resume.id, user_id, keyword="SQL")
    _upsert(db, other_resume.id, user_id, keyword="Docker")

    result = list_resume_skill_additions(resume.id, current_user=m.User(id=user_id), db=db)

    assert [a.keyword for a in result] == ["SQL"]


def test_delete_skill_addition_removes_it(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    addition = _upsert(db, resume.id, user_id, keyword="SQL")

    delete_resume_skill_addition(resume.id, addition.id, current_user=m.User(id=user_id), db=db)

    assert list_resume_skill_additions(resume.id, current_user=m.User(id=user_id), db=db) == []


def test_delete_skill_addition_404s_for_one_owned_by_someone_else(db):
    owner_id = uuid.uuid4()
    resume = _make_resume(db, owner_id, is_main=True)
    addition = _upsert(db, resume.id, owner_id, keyword="SQL")

    with pytest.raises(HTTPException) as exc_info:
        delete_resume_skill_addition(resume.id, addition.id, current_user=m.User(id=uuid.uuid4()), db=db)
    assert exc_info.value.status_code == 404


def test_apply_skill_additions_422s_when_there_are_no_drafts(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)

    with pytest.raises(HTTPException) as exc_info:
        apply_resume_skill_additions(resume.id, current_user=m.User(id=user_id), db=db)
    assert exc_info.value.status_code == 422


# --------------------------------------------------------- interview prep


def _make_interview_prep(db, resume: m.Resume, posting: m.JobPosting) -> InterviewPrep:
    prep = InterviewPrep(
        resume_id=resume.id,
        user_id=resume.user_id,
        job_posting_id=posting.id,
        content={
            "likely_questions": [{"question": "Q", "category": "technical", "approach": "A"}],
            "talking_points": ["Led the migration"],
            "questions_to_ask": ["What does success look like?"],
        },
    )
    db.add(prep)
    db.commit()
    return prep


def test_get_main_interview_prep_404s_when_none_generated_yet(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    posting = _make_job_posting(db)

    with pytest.raises(HTTPException) as exc_info:
        get_main_interview_prep(posting.id, current_user=m.User(id=user_id), db=db)
    assert exc_info.value.status_code == 404


def test_get_main_interview_prep_returns_the_latest_one(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    posting = _make_job_posting(db)
    older = _make_interview_prep(db, resume, posting)
    older.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer = _make_interview_prep(db, resume, posting)
    newer.created_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    db.commit()

    result = get_main_interview_prep(posting.id, current_user=m.User(id=user_id), db=db)

    # Called directly as a plain function (not through the real FastAPI
    # app), so this is the raw ORM row, not response_model-validated —
    # .content is still a plain dict, not InterviewPrepContent.
    assert result.id == newer.id
    assert result.content["talking_points"] == ["Led the migration"]


def test_get_main_interview_prep_uses_the_given_resume_id(db):
    user_id = uuid.uuid4()
    main_resume = _make_resume(db, user_id, is_main=True)
    other_resume = _make_resume(db, user_id, is_main=False)
    posting = _make_job_posting(db)
    for_other = _make_interview_prep(db, other_resume, posting)

    # No prep exists for the main resume — omitting resume_id 404s...
    with pytest.raises(HTTPException):
        get_main_interview_prep(posting.id, current_user=m.User(id=user_id), db=db)
    # ...but passing the other resume's id finds its prep.
    result = get_main_interview_prep(posting.id, resume_id=other_resume.id, current_user=m.User(id=user_id), db=db)
    assert result.id == for_other.id


def test_tailored_resume_text_renders_entries_not_just_flat_bullets():
    # Regression test: a section using "entries" (e.g. "Professional
    # Experience" — the normal shape for a jobs/degrees section, see
    # TAILOR_PROMPT) used to render as just its heading with nothing under
    # it, since this only read "bullets". That made a real, detailed
    # tailored resume look empty to the rescoring LLM (no dates, no
    # employers, no accomplishment bullets), tanking its score.
    content = {
        "summary": "Backend engineer.",
        "sections": [
            {"heading": "Skills", "bullets": ["Python", "SQL"], "entries": []},
            {
                "heading": "Professional Experience",
                "bullets": [],
                "entries": [
                    {
                        "title": "Senior Engineer",
                        "subtitle": "Acme Corp · 2020 - Present",
                        "bullets": ["Led the API migration", "Mentored 3 engineers"],
                    },
                    {"title": "Engineer", "subtitle": None, "bullets": []},
                ],
            },
        ],
    }
    text = _tailored_resume_text(content)
    assert "Backend engineer." in text
    assert "- Python" in text
    assert "Senior Engineer — Acme Corp · 2020 - Present" in text
    assert "- Led the API migration" in text
    assert "- Mentored 3 engineers" in text
    assert "Engineer" in text  # no subtitle, no bullets — title alone still renders


# --------------------------------------------------- structuring (docx/PDF)


def test_structure_resume_stores_structured_content(db, monkeypatch):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    monkeypatch.setattr(resumes_routes, "get_users_default_llm_key", lambda db, uid: _fake_key())
    monkeypatch.setattr(
        resumes_routes,
        "extract_resume_structure_with_llm",
        lambda *a, **k: TailoredResumeContent(
            summary="Backend engineer.",
            sections=[{"heading": "Skills", "bullets": ["Python"], "entries": []}],
            contact={"name": "Jane Doe"},
            raw_response={},
        ),
    )

    result = structure_resume(resume.id, current_user=m.User(id=user_id), db=db)

    assert result.structured_content["summary"] == "Backend engineer."
    assert result.structured_content["sections"][0]["heading"] == "Skills"
    assert result.structured_content["contact"]["name"] == "Jane Doe"


def test_structure_resume_404s_for_a_resume_owned_by_someone_else(db):
    owner_id = uuid.uuid4()
    resume = _make_resume(db, owner_id, is_main=True)

    with pytest.raises(HTTPException) as exc_info:
        structure_resume(resume.id, current_user=m.User(id=uuid.uuid4()), db=db)
    assert exc_info.value.status_code == 404


def test_structure_resume_propagates_422_when_no_default_llm_key(db, monkeypatch):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)

    def raise_no_key(db, uid):
        raise HTTPException(status_code=422, detail="Add a default LLM API key first via POST /api-keys.")

    monkeypatch.setattr(resumes_routes, "get_users_default_llm_key", raise_no_key)

    with pytest.raises(HTTPException) as exc_info:
        structure_resume(resume.id, current_user=m.User(id=user_id), db=db)
    assert exc_info.value.status_code == 422
    assert resume.structured_content is None


def test_structure_resume_502s_on_llm_error(db, monkeypatch):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    monkeypatch.setattr(resumes_routes, "get_users_default_llm_key", lambda db, uid: _fake_key())

    def raise_llm_error(*a, **k):
        raise LlmError("model returned garbage")

    monkeypatch.setattr(resumes_routes, "extract_resume_structure_with_llm", raise_llm_error)

    with pytest.raises(HTTPException) as exc_info:
        structure_resume(resume.id, current_user=m.User(id=user_id), db=db)
    assert exc_info.value.status_code == 502


# ------------------------------------------------------------- main resume download


def test_download_resume_default_format_streams_the_raw_file_unchanged(db, monkeypatch):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    monkeypatch.setattr(resumes_routes, "download_file", lambda key: b"RAW-FILE-BYTES")

    response = download_resume(resume.id, current_user=m.User(id=user_id), db=db)

    assert response.body == b"RAW-FILE-BYTES"
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")


def test_download_resume_format_pdf_422s_before_structuring(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)

    with pytest.raises(HTTPException) as exc_info:
        download_resume(resume.id, format="pdf", current_user=m.User(id=user_id), db=db)
    assert exc_info.value.status_code == 422


def test_download_resume_format_pdf_renders_from_structured_content(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    resume.structured_content = {
        "summary": "Backend engineer.",
        "sections": [{"heading": "Skills", "bullets": ["Python"], "entries": []}],
        "contact": {"name": "Jane Doe"},
    }
    db.commit()

    response = download_resume(
        resume.id, format="pdf", current_user=m.User(id=user_id, display_name="Jane Doe"), db=db
    )

    assert response.media_type == "application/pdf"
    assert response.body.startswith(b"%PDF")
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["content-disposition"].endswith('.pdf"')


def test_download_resume_format_docx_renders_from_structured_content(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    resume.structured_content = {"summary": "Backend engineer.", "sections": [], "contact": None}
    db.commit()

    response = download_resume(
        resume.id, format="docx", current_user=m.User(id=user_id, display_name="Jane Doe"), db=db
    )

    assert response.media_type == resumes_routes._TAILORED_CONTENT_TYPE
    assert response.body.startswith(b"PK")  # docx is a zip archive
    assert response.headers["content-disposition"].endswith('.docx"')


def test_download_resume_format_pdf_disposition_inline_for_preview(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    resume.structured_content = {"summary": "", "sections": [], "contact": None}
    db.commit()

    response = download_resume(
        resume.id, format="pdf", disposition="inline", current_user=m.User(id=user_id), db=db
    )

    assert response.headers["content-disposition"].startswith("inline")


# ---------------------------------------------- tailored resume / cover letter PDF


def test_download_tailored_resume_format_pdf_renders_on_the_fly(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    posting = _make_job_posting(db)
    tailored = _make_tailored_resume(
        db, user_id, resume, posting,
        content={"summary": "Backend engineer.", "sections": [], "contact": {"name": "Jane Doe"}},
    )

    response = download_tailored_resume(tailored.id, format="pdf", current_user=m.User(id=user_id), db=db)

    assert response.media_type == "application/pdf"
    assert response.body.startswith(b"%PDF")
    assert response.headers["content-disposition"].endswith('.pdf"')


def test_download_tailored_resume_default_format_is_unchanged_docx_from_storage(db, monkeypatch):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    posting = _make_job_posting(db)
    tailored = _make_tailored_resume(db, user_id, resume, posting)
    monkeypatch.setattr(resumes_routes, "download_file", lambda key: b"STORED-DOCX-BYTES")

    response = download_tailored_resume(tailored.id, current_user=m.User(id=user_id), db=db)

    assert response.body == b"STORED-DOCX-BYTES"
    assert response.media_type == resumes_routes._TAILORED_CONTENT_TYPE


def test_download_cover_letter_format_pdf_renders_on_the_fly(db):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    posting = _make_job_posting(db)
    cover_letter = _make_cover_letter(
        db, user_id, resume, posting,
        content={"greeting": "Dear Hiring Manager,", "body_paragraphs": ["I am interested."], "closing": "Sincerely,"},
    )

    response = download_cover_letter(cover_letter.id, format="pdf", current_user=m.User(id=user_id), db=db)

    assert response.media_type == "application/pdf"
    assert response.body.startswith(b"%PDF")
    assert response.headers["content-disposition"].endswith('.pdf"')


# -------------------------------------------------------- roles from structured content


def test_roles_from_structured_content_reads_from_the_experience_section():
    content = {
        "sections": [
            {
                "heading": "Professional Experience",
                "bullets": [],
                "entries": [{"title": "Senior Engineer", "subtitle": "Acme Corp · 2020-Present", "bullets": []}],
            },
            {
                "heading": "Education",
                "bullets": [],
                "entries": [{"title": "BS Computer Science", "subtitle": "MIT", "bullets": []}],
            },
        ]
    }

    roles = _roles_from_structured_content(content)

    assert roles == ["Senior Engineer — Acme Corp · 2020-Present"]


def test_roles_from_structured_content_returns_none_when_no_experience_heading_matches():
    content = {"sections": [{"heading": "Something Else", "bullets": [], "entries": [{"title": "X", "bullets": []}]}]}

    assert _roles_from_structured_content(content) is None


def test_get_resume_roles_uses_structured_content_instead_of_a_second_llm_call(db, monkeypatch):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    resume.structured_content = {
        "sections": [
            {"heading": "Experience", "bullets": [], "entries": [{"title": "Engineer", "subtitle": "Acme", "bullets": []}]}
        ]
    }
    db.commit()
    should_not_be_called = Mock(side_effect=AssertionError("should not call the LLM when structured_content exists"))
    monkeypatch.setattr(resumes_routes, "extract_resume_roles_with_llm", should_not_be_called)

    result = get_resume_roles(resume.id, current_user=m.User(id=user_id), db=db)

    assert result.roles == ["Engineer — Acme"]
    should_not_be_called.assert_not_called()


def test_get_resume_roles_falls_back_to_the_llm_when_not_yet_structured(db, monkeypatch):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    monkeypatch.setattr(resumes_routes, "get_users_default_llm_key", lambda db, uid: _fake_key())
    from app.services.resume_llm import ResumeRolesResult

    monkeypatch.setattr(
        resumes_routes, "extract_resume_roles_with_llm", lambda *a, **k: ResumeRolesResult(roles=["Engineer — Acme"])
    )

    result = get_resume_roles(resume.id, current_user=m.User(id=user_id), db=db)

    assert result.roles == ["Engineer — Acme"]


# --------------------------------- skill additions populate structured_content too


def test_apply_skill_additions_stores_structured_content_on_the_new_resume(db, monkeypatch):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    _upsert(db, resume.id, user_id, keyword="Kubernetes", target_role="General / Skills")
    monkeypatch.setattr(resumes_routes, "get_users_default_llm_key", lambda db, uid: _fake_key())
    monkeypatch.setattr(
        resumes_routes,
        "apply_skill_additions_with_llm",
        lambda *a, **k: TailoredResumeContent(
            summary="Backend engineer.",
            sections=[{"heading": "Skills", "bullets": ["Kubernetes"], "entries": []}],
            contact=None,
            raw_response={},
        ),
    )
    monkeypatch.setattr(resumes_routes, "upload_file", lambda *a, **k: None)

    new_resume = apply_resume_skill_additions(resume.id, current_user=m.User(id=user_id, display_name="Jane"), db=db)

    assert new_resume.structured_content is not None
    assert new_resume.structured_content["summary"] == "Backend engineer."
    assert new_resume.structured_content["sections"][0]["bullets"] == ["Kubernetes"]


# --------------------------------- resume versioning


def _mock_skill_additions_apply(monkeypatch):
    monkeypatch.setattr(resumes_routes, "get_users_default_llm_key", lambda db, uid: _fake_key())
    monkeypatch.setattr(
        resumes_routes,
        "apply_skill_additions_with_llm",
        lambda *a, **k: TailoredResumeContent(
            summary="Backend engineer.",
            sections=[{"heading": "Skills", "bullets": ["Kubernetes"], "entries": []}],
            contact=None,
            raw_response={},
        ),
    )
    monkeypatch.setattr(resumes_routes, "upload_file", lambda *a, **k: None)


def test_apply_skill_additions_creates_the_next_version_of_the_same_family_and_auto_promotes_it(db, monkeypatch):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    _upsert(db, resume.id, user_id, keyword="Kubernetes")
    _mock_skill_additions_apply(monkeypatch)

    new_resume = apply_resume_skill_additions(resume.id, current_user=m.User(id=user_id, display_name="Jane"), db=db)

    assert new_resume.id != resume.id
    assert new_resume.root_resume_id == resume.root_resume_id == resume.id
    assert new_resume.version_number == 2
    assert new_resume.is_main is True
    db.refresh(resume)
    assert resume.is_main is False


def test_apply_skill_additions_keeps_the_familys_original_filename_stem(db, monkeypatch):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)  # filename: resume-{n}.pdf
    stem = resume.filename.rsplit(".", 1)[0]
    _upsert(db, resume.id, user_id, keyword="Kubernetes")
    _mock_skill_additions_apply(monkeypatch)

    new_resume = apply_resume_skill_additions(resume.id, current_user=m.User(id=user_id, display_name="Jane"), db=db)

    assert new_resume.filename == f"{stem}.docx"


def test_apply_skill_additions_from_a_non_root_version_still_uses_the_familys_original_filename(db, monkeypatch):
    user_id = uuid.uuid4()
    v1 = _make_resume(db, user_id, is_main=False)  # filename: resume-{n}.pdf, the family's original
    root_stem = v1.filename.rsplit(".", 1)[0]
    v2 = _make_resume(db, user_id, is_main=True, root_resume_id=v1.id, version_number=2)
    v2.filename = "a-completely-different-name.docx"
    db.commit()
    _upsert(db, v2.id, user_id, keyword="Kubernetes")
    _mock_skill_additions_apply(monkeypatch)

    v3 = apply_resume_skill_additions(v2.id, current_user=m.User(id=user_id, display_name="Jane"), db=db)

    assert v3.filename == f"{root_stem}.docx"


def test_apply_skill_additions_always_optimizes_the_latest_version_even_when_called_via_an_older_one(db, monkeypatch):
    user_id = uuid.uuid4()
    v1 = _make_resume(db, user_id, is_main=False)
    v1.parsed_text = "OLD TEXT"
    v2 = _make_resume(db, user_id, is_main=True, root_resume_id=v1.id, version_number=2)
    v2.parsed_text = "NEW TEXT"
    db.commit()
    # Staged against the older version's id — a stale draft must still be
    # picked up and applied against the family's current content.
    _upsert(db, v1.id, user_id, keyword="Kubernetes")

    captured_resume_text = {}

    def fake_apply(resume_text, additions, **kwargs):
        captured_resume_text["value"] = resume_text
        return TailoredResumeContent(
            summary="Backend engineer.",
            sections=[{"heading": "Skills", "bullets": ["Kubernetes"], "entries": []}],
            contact=None,
            raw_response={},
        )

    monkeypatch.setattr(resumes_routes, "get_users_default_llm_key", lambda db, uid: _fake_key())
    monkeypatch.setattr(resumes_routes, "apply_skill_additions_with_llm", fake_apply)
    monkeypatch.setattr(resumes_routes, "upload_file", lambda *a, **k: None)

    new_resume = apply_resume_skill_additions(v1.id, current_user=m.User(id=user_id, display_name="Jane"), db=db)

    assert captured_resume_text["value"] == "NEW TEXT"
    assert new_resume.version_number == 3
    assert new_resume.is_main is True


def test_apply_skill_additions_applied_twice_increments_the_version_number_each_time(db, monkeypatch):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=True)
    _mock_skill_additions_apply(monkeypatch)

    _upsert(db, resume.id, user_id, keyword="Kubernetes")
    v2 = apply_resume_skill_additions(resume.id, current_user=m.User(id=user_id, display_name="Jane"), db=db)
    _upsert(db, v2.id, user_id, keyword="Terraform")
    v3 = apply_resume_skill_additions(v2.id, current_user=m.User(id=user_id, display_name="Jane"), db=db)

    assert v2.version_number == 2
    assert v3.version_number == 3
    assert v3.root_resume_id == resume.id
    assert v3.is_main is True
    db.refresh(v2)
    assert v2.is_main is False


def test_apply_skill_additions_un_mains_the_previous_main_even_in_a_different_family(db, monkeypatch):
    user_id = uuid.uuid4()
    resume = _make_resume(db, user_id, is_main=False)
    other_family_main = _make_resume(db, user_id, is_main=True)
    _upsert(db, resume.id, user_id, keyword="Kubernetes")
    _mock_skill_additions_apply(monkeypatch)

    new_resume = apply_resume_skill_additions(resume.id, current_user=m.User(id=user_id, display_name="Jane"), db=db)

    assert new_resume.is_main is True
    db.refresh(other_family_main)
    assert other_family_main.is_main is False


def test_upload_resume_self_references_as_its_own_root(db, monkeypatch):
    user_id = uuid.uuid4()
    monkeypatch.setattr(resumes_routes, "extract_text", lambda data, content_type: "Some resume text")
    monkeypatch.setattr(resumes_routes, "_structure_resume", lambda db, current_user, resume: {})
    file = UploadFile(
        file=io.BytesIO(b"pdf bytes"), filename="resume.pdf", headers=Headers({"content-type": "application/pdf"})
    )

    resume = upload_resume(file=file, current_user=m.User(id=user_id), db=db)

    assert resume.root_resume_id == resume.id
    assert resume.version_number == 1


def test_upload_resume_second_upload_starts_its_own_independent_family(db, monkeypatch):
    user_id = uuid.uuid4()
    monkeypatch.setattr(resumes_routes, "extract_text", lambda data, content_type: "Some resume text")
    monkeypatch.setattr(resumes_routes, "_structure_resume", lambda db, current_user, resume: {})

    def _upload(filename: str) -> m.Resume:
        file = UploadFile(
            file=io.BytesIO(b"pdf bytes"), filename=filename, headers=Headers({"content-type": "application/pdf"})
        )
        return upload_resume(file=file, current_user=m.User(id=user_id), db=db)

    first = _upload("first.pdf")
    second = _upload("second.pdf")

    assert second.root_resume_id == second.id
    assert second.root_resume_id != first.root_resume_id


def test_list_resumes_returns_one_row_per_family_preferring_the_current_main_version(db, monkeypatch):
    user_id = uuid.uuid4()
    v1 = _make_resume(db, user_id, is_main=False)
    v2 = _make_resume(db, user_id, is_main=True, root_resume_id=v1.id, version_number=2)
    unrelated = _make_resume(db, user_id, is_main=False)

    result = list_resumes(current_user=m.User(id=user_id), db=db)

    result_ids = {r.id for r in result}
    assert v2.id in result_ids
    assert v1.id not in result_ids
    assert unrelated.id in result_ids
    assert len(result) == 2


def test_list_resumes_falls_back_to_the_highest_version_when_no_version_is_main(db):
    user_id = uuid.uuid4()
    v1 = _make_resume(db, user_id, is_main=False)
    v2 = _make_resume(db, user_id, is_main=False, root_resume_id=v1.id, version_number=2)

    result = list_resumes(current_user=m.User(id=user_id), db=db)

    assert len(result) == 1
    assert result[0].id == v2.id


def test_get_resume_versions_lists_every_version_newest_first_regardless_of_which_id_was_asked_for(db):
    user_id = uuid.uuid4()
    v1 = _make_resume(db, user_id, is_main=False)
    v2 = _make_resume(db, user_id, is_main=True, root_resume_id=v1.id, version_number=2)

    from_root = get_resume_versions(v1.id, current_user=m.User(id=user_id), db=db)
    from_leaf = get_resume_versions(v2.id, current_user=m.User(id=user_id), db=db)

    assert [r.id for r in from_root] == [v2.id, v1.id]
    assert [r.id for r in from_leaf] == [v2.id, v1.id]


def test_get_resume_versions_404s_for_a_resume_owned_by_someone_else(db):
    resume = _make_resume(db, uuid.uuid4())

    with pytest.raises(HTTPException) as exc_info:
        get_resume_versions(resume.id, current_user=m.User(id=uuid.uuid4()), db=db)
    assert exc_info.value.status_code == 404


def test_delete_resume_on_a_non_root_version_leaves_siblings_untouched(db, monkeypatch):
    user_id = uuid.uuid4()
    monkeypatch.setattr(resumes_routes, "delete_file", lambda *a, **k: None)
    v1 = _make_resume(db, user_id, is_main=False)
    v2 = _make_resume(db, user_id, is_main=True, root_resume_id=v1.id, version_number=2)

    delete_resume(v2.id, current_user=m.User(id=user_id), db=db)
    db.commit()

    db.refresh(v1)
    assert v1.root_resume_id == v1.id
    assert db.get(m.Resume, v2.id) is None


def test_delete_resume_root_reparents_surviving_siblings_to_the_next_oldest(db, monkeypatch):
    user_id = uuid.uuid4()
    monkeypatch.setattr(resumes_routes, "delete_file", lambda *a, **k: None)
    v1 = _make_resume(db, user_id, is_main=False)
    v2 = _make_resume(db, user_id, is_main=False, root_resume_id=v1.id, version_number=2)
    v3 = _make_resume(db, user_id, is_main=True, root_resume_id=v1.id, version_number=3)

    delete_resume(v1.id, current_user=m.User(id=user_id), db=db)
    db.commit()

    assert db.get(m.Resume, v1.id) is None
    db.refresh(v2)
    db.refresh(v3)
    assert v2.root_resume_id == v2.id
    assert v3.root_resume_id == v2.id


def test_delete_resume_sole_version_removes_cleanly(db, monkeypatch):
    user_id = uuid.uuid4()
    monkeypatch.setattr(resumes_routes, "delete_file", lambda *a, **k: None)
    resume = _make_resume(db, user_id, is_main=True)

    delete_resume(resume.id, current_user=m.User(id=user_id), db=db)
    db.commit()

    assert db.get(m.Resume, resume.id) is None


def test_delete_resume_of_the_current_main_version_does_not_auto_promote_a_sibling(db, monkeypatch):
    user_id = uuid.uuid4()
    monkeypatch.setattr(resumes_routes, "delete_file", lambda *a, **k: None)
    v1 = _make_resume(db, user_id, is_main=False)
    v2 = _make_resume(db, user_id, is_main=True, root_resume_id=v1.id, version_number=2)

    delete_resume(v2.id, current_user=m.User(id=user_id), db=db)
    db.commit()

    db.refresh(v1)
    assert v1.is_main is False


# --------------------------------- default must always be the latest version


def test_update_resume_422s_when_setting_a_non_latest_version_as_default(db):
    user_id = uuid.uuid4()
    v1 = _make_resume(db, user_id, is_main=False)
    _make_resume(db, user_id, is_main=False, root_resume_id=v1.id, version_number=2)

    with pytest.raises(HTTPException) as exc_info:
        update_resume(v1.id, ResumeUpdate(is_main=True), current_user=m.User(id=user_id), db=db)
    assert exc_info.value.status_code == 422


def test_update_resume_allows_setting_the_latest_version_as_default(db):
    user_id = uuid.uuid4()
    v1 = _make_resume(db, user_id, is_main=False)
    v2 = _make_resume(db, user_id, is_main=False, root_resume_id=v1.id, version_number=2)
    other = _make_resume(db, user_id, is_main=True)

    result = update_resume(v2.id, ResumeUpdate(is_main=True), current_user=m.User(id=user_id), db=db)

    assert result.is_main is True
    db.refresh(other)
    assert other.is_main is False


# --------------------------------- restoring an older version


def _mock_storage(monkeypatch):
    monkeypatch.setattr(resumes_routes, "download_file", lambda key: b"old bytes")
    monkeypatch.setattr(resumes_routes, "upload_file", lambda *a, **k: None)


def test_restore_creates_a_new_latest_version_with_the_old_version_content(db, monkeypatch):
    user_id = uuid.uuid4()
    _mock_storage(monkeypatch)
    v1 = _make_resume(db, user_id, is_main=False)
    v1.parsed_text = "Version 1 text"
    db.commit()
    _make_resume(db, user_id, is_main=False, root_resume_id=v1.id, version_number=2)

    restored = restore_resume_version(v1.id, current_user=m.User(id=user_id), db=db)

    assert restored.id not in (v1.id,)
    assert restored.root_resume_id == v1.id
    assert restored.version_number == 3
    assert restored.parsed_text == "Version 1 text"
    assert restored.filename == v1.filename


def test_restore_promotes_to_default_when_its_family_was_already_default(db, monkeypatch):
    user_id = uuid.uuid4()
    _mock_storage(monkeypatch)
    v1 = _make_resume(db, user_id, is_main=False)
    v2 = _make_resume(db, user_id, is_main=True, root_resume_id=v1.id, version_number=2)

    restored = restore_resume_version(v1.id, current_user=m.User(id=user_id), db=db)

    assert restored.is_main is True
    db.refresh(v2)
    assert v2.is_main is False


def test_restore_does_not_promote_when_its_family_was_not_the_default(db, monkeypatch):
    user_id = uuid.uuid4()
    _mock_storage(monkeypatch)
    other_family_main = _make_resume(db, user_id, is_main=True)
    v1 = _make_resume(db, user_id, is_main=False)
    _make_resume(db, user_id, is_main=False, root_resume_id=v1.id, version_number=2)

    restored = restore_resume_version(v1.id, current_user=m.User(id=user_id), db=db)

    assert restored.is_main is False
    db.refresh(other_family_main)
    assert other_family_main.is_main is True


def test_restore_422s_when_the_resume_is_already_the_latest_version(db, monkeypatch):
    user_id = uuid.uuid4()
    _mock_storage(monkeypatch)
    v1 = _make_resume(db, user_id, is_main=False)
    v2 = _make_resume(db, user_id, is_main=False, root_resume_id=v1.id, version_number=2)

    with pytest.raises(HTTPException) as exc_info:
        restore_resume_version(v2.id, current_user=m.User(id=user_id), db=db)
    assert exc_info.value.status_code == 422


def test_restore_404s_for_a_resume_owned_by_someone_else(db):
    resume = _make_resume(db, uuid.uuid4())

    with pytest.raises(HTTPException) as exc_info:
        restore_resume_version(resume.id, current_user=m.User(id=uuid.uuid4()), db=db)
    assert exc_info.value.status_code == 404


# --------------------------------- score history survives new versions


def test_get_resume_score_history_includes_scores_from_earlier_versions_in_the_same_family(db):
    user_id = uuid.uuid4()
    v1 = _make_resume(db, user_id, is_main=False)
    v2 = _make_resume(db, user_id, is_main=True, root_resume_id=v1.id, version_number=2)
    job = _make_job_posting(db)
    old_score = _make_score(db, v1, job, overall_score=55, missing_keywords=["Kubernetes"])

    result = get_resume_score_history(v2.id, current_user=m.User(id=user_id), db=db)

    assert [e.id for e in result.entries] == [old_score.id]


def test_get_resume_score_history_via_an_older_version_id_still_sees_the_whole_family(db):
    user_id = uuid.uuid4()
    v1 = _make_resume(db, user_id, is_main=False)
    v2 = _make_resume(db, user_id, is_main=True, root_resume_id=v1.id, version_number=2)
    job = _make_job_posting(db)
    new_score = _make_score(db, v2, job, overall_score=80)

    result = get_resume_score_history(v1.id, current_user=m.User(id=user_id), db=db)

    assert [e.id for e in result.entries] == [new_score.id]


def test_missing_keywords_summary_includes_earlier_versions_scores_too(db):
    # A keyword only surfaces once it's recurred across 2+ distinct jobs
    # (see get_missing_keywords_summary's len(jobs) >= 2 threshold) — split
    # across v1 and v2 of the same family to prove an older version's
    # scoring history still counts once a newer version exists.
    user_id = uuid.uuid4()
    v1 = _make_resume(db, user_id, is_main=False)
    v2 = _make_resume(db, user_id, is_main=True, root_resume_id=v1.id, version_number=2)
    job_a = _make_job_posting(db, title="Backend Engineer", company_name="Acme")
    job_b = _make_job_posting(db, title="Platform Engineer", company_name="Globex")
    _make_score(db, v1, job_a, missing_keywords=["Kubernetes"])
    _make_score(db, v2, job_b, missing_keywords=["Kubernetes"])

    result = get_missing_keywords_summary(current_user=m.User(id=user_id), db=db)

    assert [e.keyword for e in result.entries] == ["Kubernetes"]
    assert {j.job_title for j in result.entries[0].jobs} == {"Backend Engineer", "Platform Engineer"}
