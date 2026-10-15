import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes.resumes import _stamp_application_pointer
from app.db.base import Base
from app.models.enums import AutoScoreStatus
from app.models.resume import CoverLetter, TailoredResumeScore
from app.services import auto_score
from app.services.jobs import parse_scan_message
from app.services.llm_client import LlmError


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            m.User.__table__,
            m.Resume.__table__,
            m.ResumeScore.__table__,
            m.TailoredResume.__table__,
            TailoredResumeScore.__table__,
            CoverLetter.__table__,
            m.JobPostingUrl.__table__,
            m.JobPosting.__table__,
            m.Company.__table__,
            m.UserJobApplication.__table__,
            m.AutoScoreRun.__table__,
        ],
    )
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture
def setup(db):
    user = m.User(email=f"{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    resume_id = uuid.uuid4()
    resume = m.Resume(
        id=resume_id,
        user_id=user.id,
        filename="r.pdf",
        content_type="application/pdf",
        storage_key="k",
        parsed_text="Resume",
        is_main=True,
        root_resume_id=resume_id,
        version_number=1,
    )
    url_row = m.JobPostingUrl(
        url="https://example.com/j", normalized_url="https://example.com/j", url_hash=str(uuid.uuid4()), domain="example.com"
    )
    db.add_all([resume, url_row])
    db.flush()
    posting = m.JobPosting(url_id=url_row.id, title="Engineer", company_name="Acme")
    db.add(posting)
    db.commit()
    return user, resume, posting


def _fake_llm(monkeypatch, db, *, resume_score: int, tailored_score: int = 0, fail_at: str | None = None):
    """Stand-ins for the three LLM actions that write real rows, so the
    run's pointers/application handling is exercised for real."""
    calls = []

    def score(db_, user, resume, posting, create_application=True):
        calls.append("score")
        if fail_at == "score":
            raise LlmError("boom")
        row = m.ResumeScore(
            resume_id=resume.id, user_id=user.id, job_posting_id=posting.id, overall_score=resume_score,
            matched_keywords=[], missing_keywords=[], summary="",
        )
        db_.add(row)
        db_.flush()
        _stamp_application_pointer(db_, user.id, posting.id, create=create_application, latest_score_id=row.id)
        return row

    def tailor(db_, user, resume, posting, create_application=True):
        calls.append("tailor")
        if fail_at == "tailor":
            raise HTTPException(status_code=402, detail="No AI access.")
        if fail_at == "storage":
            raise ConnectionError("Could not connect to the endpoint URL")
        row = m.TailoredResume(
            resume_id=resume.id, user_id=user.id, job_posting_id=posting.id, content={}, storage_key="t", filename="t.docx"
        )
        db_.add(row)
        db_.flush()
        return row

    def score_tailored(db_, user, tailored, posting, create_application=True):
        calls.append("score_tailored")
        row = TailoredResumeScore(
            tailored_resume_id=tailored.id, user_id=user.id, job_posting_id=posting.id, overall_score=tailored_score,
            matched_keywords=[], missing_keywords=[], summary="",
        )
        db_.add(row)
        db_.flush()
        return row

    monkeypatch.setattr(auto_score, "quick_score_resume", score)
    monkeypatch.setattr(auto_score, "tailor_resume", tailor)
    monkeypatch.setattr(auto_score, "quick_score_tailored_resume", score_tailored)
    monkeypatch.setattr(auto_score, "enqueue_auto_score", lambda run_id: None)
    return calls


def _application(db, user, posting):
    return db.scalar(
        select(m.UserJobApplication).where(
            m.UserJobApplication.user_id == user.id, m.UserJobApplication.job_posting_id == posting.id
        )
    )


def _run(db, user, resume, posting):
    run = auto_score.start_auto_score(db, user, resume, posting)
    auto_score.run_auto_score(db, run.id)
    db.refresh(run)
    return run


def test_below_tailor_threshold_stops_after_first_score_and_creates_no_application(db, setup, monkeypatch):
    user, resume, posting = setup
    calls = _fake_llm(monkeypatch, db, resume_score=74)

    run = _run(db, user, resume, posting)

    assert run.status == AutoScoreStatus.BELOW_THRESHOLD
    assert calls == ["score"]
    assert _application(db, user, posting) is None


def test_tailored_but_short_of_ready_creates_no_application(db, setup, monkeypatch):
    user, resume, posting = setup
    calls = _fake_llm(monkeypatch, db, resume_score=78, tailored_score=84)

    run = _run(db, user, resume, posting)

    assert run.status == AutoScoreStatus.NOT_READY
    assert run.best_score == 84
    assert calls == ["score", "tailor", "score_tailored"]
    assert _application(db, user, posting) is None


def test_existing_application_is_kept_but_not_marked_ready(db, setup, monkeypatch):
    user, resume, posting = setup
    db.add(m.UserJobApplication(user_id=user.id, job_posting_id=posting.id, ready_to_apply_at=datetime.now(timezone.utc)))
    db.commit()
    _fake_llm(monkeypatch, db, resume_score=80, tailored_score=82)

    run = _run(db, user, resume, posting)

    application = _application(db, user, posting)
    assert run.status == AutoScoreStatus.NOT_READY
    assert application is not None
    assert application.ready_to_apply_at is None
    assert application.latest_score_id == run.resume_score_id


def test_best_score_at_ready_threshold_saves_and_marks_application(db, setup, monkeypatch):
    user, resume, posting = setup
    _fake_llm(monkeypatch, db, resume_score=86, tailored_score=80)

    run = _run(db, user, resume, posting)

    application = _application(db, user, posting)
    assert run.status == AutoScoreStatus.READY
    assert run.best_score == 86
    assert application.ready_to_apply_at is not None
    assert application.latest_score_id == run.resume_score_id
    assert application.latest_tailored_resume_id == run.tailored_resume_id
    assert application.latest_tailored_resume_score_id == run.tailored_score_id
    assert application.selected_resume_id == resume.id


def test_llm_failure_ends_run_failed_without_raising(db, setup, monkeypatch):
    user, resume, posting = setup
    _fake_llm(monkeypatch, db, resume_score=90, fail_at="tailor")

    run = _run(db, user, resume, posting)

    assert run.status == AutoScoreStatus.FAILED
    assert run.error == "No AI access."


def test_unexpected_error_ends_run_failed_without_raising_or_retrying(db, setup, monkeypatch):
    user, resume, posting = setup
    calls = _fake_llm(monkeypatch, db, resume_score=90, fail_at="storage")

    run = _run(db, user, resume, posting)
    # Pub/Sub redelivering the same message must not tailor again.
    auto_score.run_auto_score(db, run.id)

    db.refresh(run)
    assert run.status == AutoScoreStatus.FAILED
    assert calls == ["score", "tailor"]


def _mid_tailoring(db, user, resume, posting, *, started_ago: timedelta) -> m.AutoScoreRun:
    """A run whose scoring finished and whose tailoring an earlier delivery
    committed as started `started_ago`, with no result yet."""
    run = auto_score.start_auto_score(db, user, resume, posting)
    score = m.ResumeScore(
        resume_id=resume.id, user_id=user.id, job_posting_id=posting.id, overall_score=90,
        matched_keywords=[], missing_keywords=[], summary="",
    )
    db.add(score)
    db.flush()
    run.resume_score_id = score.id
    run.resume_score = 90
    run.status = AutoScoreStatus.TAILORING
    run.updated_at = datetime.now(timezone.utc) - started_ago
    db.commit()
    return run


def test_redelivery_leaves_a_step_another_delivery_is_still_running_alone(db, setup, monkeypatch):
    user, resume, posting = setup
    calls = _fake_llm(monkeypatch, db, resume_score=90, tailored_score=88)
    run = _mid_tailoring(db, user, resume, posting, started_ago=timedelta(seconds=10))

    auto_score.run_auto_score(db, run.id)

    db.refresh(run)
    assert run.status == AutoScoreStatus.TAILORING
    assert calls == []


def test_redelivery_reruns_a_step_whose_delivery_died(db, setup, monkeypatch):
    user, resume, posting = setup
    calls = _fake_llm(monkeypatch, db, resume_score=90, tailored_score=88)
    run = _mid_tailoring(db, user, resume, posting, started_ago=timedelta(minutes=5))

    auto_score.run_auto_score(db, run.id)

    db.refresh(run)
    assert run.status == AutoScoreStatus.READY
    assert calls == ["tailor", "score_tailored"]


def test_polling_a_run_whose_step_died_sends_it_to_the_worker_again(db, setup, monkeypatch):
    user, resume, posting = setup
    _fake_llm(monkeypatch, db, resume_score=90)
    run = _mid_tailoring(db, user, resume, posting, started_ago=timedelta(minutes=5))
    requeued = []
    monkeypatch.setattr(auto_score, "enqueue_auto_score", requeued.append)

    auto_score.latest_run(db, user.id, posting.id)

    assert requeued == [run.id]


def test_polling_a_run_still_within_its_lease_does_not_requeue_it(db, setup, monkeypatch):
    user, resume, posting = setup
    _fake_llm(monkeypatch, db, resume_score=90)
    _mid_tailoring(db, user, resume, posting, started_ago=timedelta(seconds=10))
    requeued = []
    monkeypatch.setattr(auto_score, "enqueue_auto_score", requeued.append)

    auto_score.latest_run(db, user.id, posting.id)

    assert requeued == []


def test_redelivered_run_resumes_after_its_last_finished_stage(db, setup, monkeypatch):
    user, resume, posting = setup
    calls = _fake_llm(monkeypatch, db, resume_score=90, tailored_score=88)
    run = auto_score.start_auto_score(db, user, resume, posting)
    score = m.ResumeScore(
        resume_id=resume.id, user_id=user.id, job_posting_id=posting.id, overall_score=90,
        matched_keywords=[], missing_keywords=[], summary="",
    )
    db.add(score)
    db.flush()
    # Scoring finished and was committed; the worker died before tailoring started.
    run.status = AutoScoreStatus.SCORING
    run.resume_score_id = score.id
    run.resume_score = 90
    db.commit()

    auto_score.run_auto_score(db, run.id)

    db.refresh(run)
    assert run.status == AutoScoreStatus.READY
    assert calls == ["tailor", "score_tailored"]


def test_redelivered_finished_run_is_a_no_op(db, setup, monkeypatch):
    user, resume, posting = setup
    calls = _fake_llm(monkeypatch, db, resume_score=60)
    run = _run(db, user, resume, posting)

    auto_score.run_auto_score(db, run.id)

    assert calls == ["score"]


def test_start_409s_while_a_run_is_in_progress(db, setup, monkeypatch):
    user, resume, posting = setup
    _fake_llm(monkeypatch, db, resume_score=60)
    auto_score.start_auto_score(db, user, resume, posting)

    with pytest.raises(HTTPException) as exc_info:
        auto_score.start_auto_score(db, user, resume, posting)
    assert exc_info.value.status_code == 409


def test_stale_in_progress_run_reads_as_failed(db, setup, monkeypatch):
    user, resume, posting = setup
    _fake_llm(monkeypatch, db, resume_score=60)
    run = auto_score.start_auto_score(db, user, resume, posting)
    run.created_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    db.commit()

    assert auto_score.latest_run(db, user.id, posting.id).status == AutoScoreStatus.FAILED


def test_parse_scan_message_recognises_auto_score():
    run_id = uuid.uuid4()
    assert parse_scan_message(f'{{"auto_score_run_id": "{run_id}"}}'.encode()) == ("auto_score", run_id)
