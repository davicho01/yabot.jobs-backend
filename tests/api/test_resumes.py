import itertools
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes.resumes import _resolve_resume, get_main_interview_prep, get_resume_score_history
from app.db.base import Base
from app.models.resume import InterviewPrep

_counter = itertools.count()


@pytest.fixture
def db() -> Session:
    # Local db fixture (not tests/api/conftest.py's, which only has
    # SavedSearch) — _resolve_resume/get_resume_score_history need Resume,
    # ResumeScore and (for the score history's job title/company) JobPosting;
    # get_main_interview_prep also needs InterviewPrep.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            m.Resume.__table__,
            m.ResumeScore.__table__,
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
