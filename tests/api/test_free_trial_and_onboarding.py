import itertools
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes import resumes as resumes_routes
from app.api.routes.onboarding import dismiss, read_ai_access, read_onboarding
from app.api.routes.resumes import evaluate_main_resume, score_main_resume
from app.core.config import settings
from app.db.base import Base
from app.services import ai_access
from app.services.llm_client import LlmError
from app.services.resume_llm import ResumeEvaluationResult, ResumeQuickScoreResult

_counter = itertools.count()


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            m.User.__table__,
            m.UserApiKey.__table__,
            m.Resume.__table__,
            m.ResumeScore.__table__,
            m.JobPostingUrl.__table__,
            m.JobPosting.__table__,
            m.UserJobApplication.__table__,
        ],
    )
    with engine.begin() as conn:
        # Same partial-index shim as tests/api/test_resumes.py: SQLite drops
        # the Postgres-only WHERE and would otherwise make these indexes
        # plain unique-per-user.
        conn.execute(text("DROP INDEX IF EXISTS uq_resumes_one_main_per_user"))
        conn.execute(text("DROP INDEX IF EXISTS uq_user_api_keys_one_default_per_user"))
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture
def trial_on(monkeypatch):
    monkeypatch.setattr(settings, "system_llm_provider", "anthropic")
    monkeypatch.setattr(settings, "system_llm_model", "system-model")
    monkeypatch.setattr(settings, "system_llm_api_key", "system-key")
    monkeypatch.setattr(settings, "free_evaluation_limit", 2)


@pytest.fixture
def trial_off(monkeypatch):
    monkeypatch.setattr(settings, "system_llm_provider", None)
    monkeypatch.setattr(settings, "system_llm_api_key", None)


@pytest.fixture
def fake_llm(monkeypatch):
    calls = []

    def quick_score(*args, **kwargs):
        calls.append(("score", kwargs))
        return ResumeQuickScoreResult(overall_score=80, matched_keywords=["python"], raw_response={"ok": True})

    def evaluate(*args, **kwargs):
        calls.append(("evaluate", kwargs))
        return ResumeEvaluationResult(category_scores=[{"category": "skills", "score": 80}], raw_response={})

    monkeypatch.setattr(resumes_routes, "quick_score_resume_with_llm", quick_score)
    monkeypatch.setattr(resumes_routes, "evaluate_resume_with_llm", evaluate)
    return calls


def _make_user(db) -> m.User:
    user = m.User(id=uuid.uuid4(), email=f"{uuid.uuid4()}@example.com")
    db.add(user)
    db.commit()
    return user


def _make_key(db, user: m.User) -> m.UserApiKey:
    key = m.UserApiKey(user_id=user.id, provider="openai", label="default", model="own-model", is_default=True)
    key.set_plaintext_key("own-key")
    db.add(key)
    db.commit()
    return key


def _make_resume(db, user: m.User) -> m.Resume:
    n = next(_counter)
    resume_id = uuid.uuid4()
    resume = m.Resume(
        id=resume_id,
        user_id=user.id,
        filename=f"resume-{n}.pdf",
        content_type="application/pdf",
        storage_key=f"resumes/{user.id}/{n}",
        parsed_text="Python engineer",
        is_main=True,
        root_resume_id=resume_id,
    )
    db.add(resume)
    db.commit()
    return resume


def _make_posting(db) -> m.JobPosting:
    n = next(_counter)
    url_row = m.JobPostingUrl(
        url=f"https://example.com/jobs/{n}",
        normalized_url=f"https://example.com/jobs/{n}",
        url_hash=f"trial-hash-{n}",
        domain="example.com",
    )
    db.add(url_row)
    db.flush()
    posting = m.JobPosting(url_id=url_row.id, title="Engineer", company_name="Acme", description="Python")
    db.add(posting)
    db.commit()
    return posting


def _score(db, user, posting):
    return score_main_resume(posting.id, resume_id=None, current_user=user, db=db)


# ------------------------------------------------------------------ ai_access


def test_own_key_is_used_and_no_free_evaluation_spent(db, trial_on):
    user = _make_user(db)
    _make_key(db, user)

    credentials = ai_access.use_free_evaluation(db, user)

    assert credentials.api_key == "own-key"
    assert credentials.is_free_trial is False
    assert user.free_evaluations_used == 0


def test_no_key_and_no_trial_asks_for_a_key(db, trial_off):
    user = _make_user(db)

    with pytest.raises(HTTPException) as exc_info:
        ai_access.use_free_evaluation(db, user)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == ai_access.NO_KEY_MESSAGE


def test_trial_limit_zero_disables_it(db, trial_on, monkeypatch):
    monkeypatch.setattr(settings, "free_evaluation_limit", 0)
    assert ai_access.free_trial_enabled() is False


def test_trial_needs_a_model_unless_anthropic(trial_on, monkeypatch):
    monkeypatch.setattr(settings, "system_llm_model", None)
    assert ai_access.free_trial_enabled() is True
    monkeypatch.setattr(settings, "system_llm_provider", "openai")
    assert ai_access.free_trial_enabled() is False


def test_free_evaluations_are_spent_until_the_limit(db, trial_on):
    user = _make_user(db)

    first = ai_access.use_free_evaluation(db, user)
    ai_access.use_free_evaluation(db, user)

    assert first.api_key == "system-key"
    assert first.is_free_trial is True
    assert user.free_evaluations_used == 2
    with pytest.raises(HTTPException) as exc_info:
        ai_access.use_free_evaluation(db, user)
    assert exc_info.value.status_code == 422
    assert "used all 2 free evaluations" in exc_info.value.detail
    assert user.free_evaluations_used == 2


# ------------------------------------------------------------ score/evaluate routes


def test_score_on_free_trial_marks_the_score_and_spends_one(db, trial_on, fake_llm):
    user = _make_user(db)
    _make_resume(db, user)
    posting = _make_posting(db)

    score = _score(db, user, posting)

    assert score.overall_score == 80
    assert score.raw_response["free_trial"] is True
    assert user.free_evaluations_used == 1
    assert fake_llm[0][1]["api_key"] == "system-key"


def test_score_with_own_key_is_not_marked_free(db, trial_on, fake_llm):
    user = _make_user(db)
    _make_key(db, user)
    _make_resume(db, user)
    posting = _make_posting(db)

    score = _score(db, user, posting)

    assert "free_trial" not in score.raw_response
    assert user.free_evaluations_used == 0
    assert fake_llm[0][1]["api_key"] == "own-key"


def test_score_llm_failure_is_a_502(db, trial_on, monkeypatch):
    # get_db rolls the request back on this exception, which is what
    # returns the free evaluation — see ai_access.use_free_evaluation.
    user = _make_user(db)
    _make_resume(db, user)
    posting = _make_posting(db)

    def boom(*args, **kwargs):
        raise LlmError("down")

    monkeypatch.setattr(resumes_routes, "quick_score_resume_with_llm", boom)

    with pytest.raises(HTTPException) as exc_info:
        _score(db, user, posting)
    assert exc_info.value.status_code == 502


def test_first_breakdown_of_a_free_score_is_included(db, trial_on, fake_llm):
    user = _make_user(db)
    _make_resume(db, user)
    posting = _make_posting(db)
    _score(db, user, posting)

    score = evaluate_main_resume(posting.id, resume_id=None, current_user=user, db=db)

    assert score.category_scores == [{"category": "skills", "score": 80}]
    assert score.raw_response["free_trial"] is True
    assert user.free_evaluations_used == 1

    # Re-running it isn't.
    with pytest.raises(HTTPException) as exc_info:
        evaluate_main_resume(posting.id, resume_id=None, current_user=user, db=db)
    assert exc_info.value.status_code == 422


def test_breakdown_of_a_score_made_with_a_removed_key_needs_a_key(db, trial_on, fake_llm):
    user = _make_user(db)
    key = _make_key(db, user)
    _make_resume(db, user)
    posting = _make_posting(db)
    _score(db, user, posting)
    db.delete(key)
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        evaluate_main_resume(posting.id, resume_id=None, current_user=user, db=db)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == ai_access.NO_KEY_MESSAGE


# ------------------------------------------------------------------ onboarding


def _steps(result) -> dict[str, bool]:
    return {step.key: step.done for step in result.steps}


def test_onboarding_for_a_brand_new_user(db, trial_off):
    user = _make_user(db)

    result = read_onboarding(current_user=user, db=db)

    assert [step.key for step in result.steps] == ["resume", "ai_access", "application", "evaluation"]
    assert not any(_steps(result).values())
    assert result.completed_at is None
    assert result.dismissed_at is None


def test_free_trial_counts_as_ai_access(db, trial_on):
    user = _make_user(db)
    assert _steps(read_onboarding(current_user=user, db=db))["ai_access"] is True

    user.free_evaluations_used = settings.free_evaluation_limit
    db.commit()
    assert _steps(read_onboarding(current_user=user, db=db))["ai_access"] is False


def test_onboarding_completes_and_stays_completed(db, trial_on, fake_llm):
    user = _make_user(db)
    _make_resume(db, user)
    posting = _make_posting(db)
    db.add(m.UserJobApplication(user_id=user.id, job_posting_id=posting.id))
    db.commit()
    _score(db, user, posting)

    result = read_onboarding(current_user=user, db=db)
    assert all(_steps(result).values())
    assert result.completed_at is not None

    # Running out of free evaluations later doesn't reopen the checklist.
    user.free_evaluations_used = settings.free_evaluation_limit
    db.commit()
    later = read_onboarding(current_user=user, db=db)
    assert _steps(later)["ai_access"] is False
    # (SQLite hands the timestamp back without its tzinfo.)
    assert later.completed_at.replace(tzinfo=None) == result.completed_at.replace(tzinfo=None)


def test_dismiss_onboarding(db, trial_off):
    user = _make_user(db)

    result = dismiss(current_user=user, db=db)
    again = dismiss(current_user=user, db=db)

    assert result.dismissed_at is not None
    assert again.dismissed_at == result.dismissed_at


def test_ai_access_summary(db, trial_on):
    user = _make_user(db)
    user.free_evaluations_used = 1
    db.commit()

    summary = read_ai_access(current_user=user, db=db)

    assert summary.has_own_key is False
    assert summary.free_trial_enabled is True
    assert summary.free_evaluation_limit == 2
    assert summary.free_evaluations_remaining == 1

    _make_key(db, user)
    assert read_ai_access(current_user=user, db=db).has_own_key is True
