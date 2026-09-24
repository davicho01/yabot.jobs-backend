import itertools
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes.resumes import (
    _resolve_resume,
    _tailored_resume_text,
    apply_resume_skill_additions,
    delete_resume_skill_addition,
    get_main_interview_prep,
    get_missing_keywords_summary,
    get_resume_score_history,
    list_resume_skill_additions,
    upsert_resume_skill_addition,
)
from app.db.base import Base
from app.models.resume import InterviewPrep
from app.schemas.resume import ResumeSkillAdditionUpsert

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


def _make_resume(db, user_id: uuid.UUID, *, is_main: bool = False) -> m.Resume:
    n = next(_counter)
    resume = m.Resume(
        user_id=user_id,
        filename=f"resume-{n}.pdf",
        content_type="application/pdf",
        storage_key=f"resumes/{user_id}/{n}",
        parsed_text=f"Resume text {n}",
        is_main=is_main,
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


def test_missing_keywords_summary_defaults_to_the_main_resume(db):
    user_id = uuid.uuid4()
    main = _make_resume(db, user_id, is_main=True)
    other = _make_resume(db, user_id, is_main=False)
    # SQL recurs across 2 jobs scored against the main resume; the other
    # resume's own recurring gap ("Kubernetes") must not bleed in when no
    # resume_id is given — each resume's scoring history is its own thing.
    _make_score(db, main, _make_job_posting(db), missing_keywords=["SQL", "Docker"])
    _make_score(db, main, _make_job_posting(db), missing_keywords=["sql"])
    _make_score(db, other, _make_job_posting(db), missing_keywords=["Kubernetes"])
    _make_score(db, other, _make_job_posting(db), missing_keywords=["Kubernetes"])

    result = get_missing_keywords_summary(current_user=m.User(id=user_id), db=db)

    by_keyword = {e.keyword: e.count for e in result.entries}
    assert by_keyword == {"SQL": 2}


def test_missing_keywords_summary_uses_the_given_resume_id(db):
    user_id = uuid.uuid4()
    main = _make_resume(db, user_id, is_main=True)
    other = _make_resume(db, user_id, is_main=False)
    _make_score(db, main, _make_job_posting(db), missing_keywords=["SQL"])
    _make_score(db, other, _make_job_posting(db), missing_keywords=["Kubernetes"])
    _make_score(db, other, _make_job_posting(db), missing_keywords=["Kubernetes"])

    result = get_missing_keywords_summary(resume_id=other.id, current_user=m.User(id=user_id), db=db)

    by_keyword = {e.keyword: e.count for e in result.entries}
    assert by_keyword == {"Kubernetes": 2}


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


def test_missing_keywords_summary_404s_for_a_resume_owned_by_someone_else(db):
    owner_id = uuid.uuid4()
    resume = _make_resume(db, owner_id, is_main=True)

    with pytest.raises(HTTPException) as exc_info:
        get_missing_keywords_summary(resume_id=resume.id, current_user=m.User(id=uuid.uuid4()), db=db)
    assert exc_info.value.status_code == 404


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
