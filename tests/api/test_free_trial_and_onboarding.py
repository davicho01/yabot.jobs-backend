import itertools
import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes import resumes as resumes_routes
from app.api.routes.onboarding import ai_access_seen, dismiss, read_ai_access, read_onboarding
from app.api.routes.resumes import evaluate_main_resume, generate_main_tailored_resume, score_main_resume
from app.core.config import settings
from app.db.base import Base
from app.services import ai_access
from app.services.llm_client import LlmError
from app.services.resume_llm import ResumeEvaluationResult, ResumeQuickScoreResult, TailoredResumeContent

_counter = itertools.count()


@pytest.fixture(autouse=True)
def real_encryption_key(monkeypatch):
    # These tests store real UserApiKey rows, which encrypts them. CI's
    # API_KEY_ENCRYPTION_KEY is a deliberate non-key placeholder, so give
    # each test a throwaway valid one instead of depending on the local .env.
    monkeypatch.setattr(settings, "api_key_encryption_key", Fernet.generate_key().decode())


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
            m.JobPosting.__table__, m.Company.__table__,
            m.UserJobApplication.__table__,
            m.FreeTrialJob.__table__,
            m.TailoredResume.__table__,
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

    credentials = ai_access.job_llm_credentials(db, user, uuid.uuid4())

    assert credentials.api_key == "own-key"
    assert credentials.is_free_trial is False
    assert user.free_evaluations_used == 0


def test_no_key_and_no_trial_asks_for_a_key(db, trial_off):
    user = _make_user(db)

    with pytest.raises(HTTPException) as exc_info:
        ai_access.job_llm_credentials(db, user, uuid.uuid4())

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == ai_access.no_access_message()


def test_trial_limit_zero_disables_it(db, trial_on, monkeypatch):
    monkeypatch.setattr(settings, "free_evaluation_limit", 0)
    assert ai_access.free_trial_enabled() is False


def test_trial_needs_a_model_unless_anthropic(trial_on, monkeypatch):
    monkeypatch.setattr(settings, "system_llm_model", None)
    assert ai_access.free_trial_enabled() is True
    monkeypatch.setattr(settings, "system_llm_provider", "openai")
    assert ai_access.free_trial_enabled() is False


def test_each_new_job_spends_one_free_evaluation_until_the_limit(db, trial_on):
    user = _make_user(db)
    job_a, job_b, job_c = _make_posting(db), _make_posting(db), _make_posting(db)

    first = ai_access.job_llm_credentials(db, user, job_a.id)
    ai_access.job_llm_credentials(db, user, job_b.id)

    assert first.api_key == "system-key"
    assert first.is_free_trial is True
    assert user.free_evaluations_used == 2
    assert set(ai_access.free_trial_job_ids(db, user)) == {job_a.id, job_b.id}
    with pytest.raises(HTTPException) as exc_info:
        ai_access.job_llm_credentials(db, user, job_c.id)
    assert exc_info.value.status_code == 422
    assert "used all 2 free evaluations" in exc_info.value.detail


def test_an_unlocked_job_stays_free_after_the_limit(db, trial_on):
    user = _make_user(db)
    job = _make_posting(db)
    ai_access.job_llm_credentials(db, user, job.id)
    ai_access.job_llm_credentials(db, user, _make_posting(db).id)

    # Out of new unlocks, but everything on an unlocked job keeps working.
    for _ in range(3):
        assert ai_access.job_llm_credentials(db, user, job.id).is_free_trial is True
    assert user.free_evaluations_used == 2


def test_resume_wide_features_are_not_covered_by_the_trial(db, trial_on):
    user = _make_user(db)

    with pytest.raises(HTTPException) as exc_info:
        ai_access.resolve_llm_credentials(db, user)
    assert exc_info.value.status_code == 422


# ------------------------------------------------------------ routes


def test_score_on_free_trial_unlocks_the_job(db, trial_on, fake_llm):
    user = _make_user(db)
    _make_resume(db, user)
    posting = _make_posting(db)

    score = _score(db, user, posting)

    assert score.overall_score == 80
    assert user.free_evaluations_used == 1
    assert ai_access.free_trial_job_ids(db, user) == [posting.id]
    assert fake_llm[0][1]["api_key"] == "system-key"


def test_score_with_own_key_spends_nothing(db, trial_on, fake_llm):
    user = _make_user(db)
    _make_key(db, user)
    _make_resume(db, user)
    posting = _make_posting(db)

    _score(db, user, posting)

    assert user.free_evaluations_used == 0
    assert ai_access.free_trial_job_ids(db, user) == []
    assert fake_llm[0][1]["api_key"] == "own-key"


def test_score_llm_failure_is_a_502(db, trial_on, monkeypatch):
    # get_db rolls the request back on this exception, which is what
    # returns the free evaluation and the unlock.
    user = _make_user(db)
    _make_resume(db, user)
    posting = _make_posting(db)

    def boom(*args, **kwargs):
        raise LlmError("down")

    monkeypatch.setattr(resumes_routes, "quick_score_resume_with_llm", boom)

    with pytest.raises(HTTPException) as exc_info:
        _score(db, user, posting)
    assert exc_info.value.status_code == 502


def test_breakdowns_on_an_unlocked_job_are_free_and_repeatable(db, trial_on, fake_llm):
    user = _make_user(db)
    _make_resume(db, user)
    posting = _make_posting(db)
    _score(db, user, posting)

    for _ in range(2):
        score = evaluate_main_resume(posting.id, resume_id=None, current_user=user, db=db)

    assert score.category_scores == [{"category": "skills", "score": 80}]
    assert user.free_evaluations_used == 1


def test_tailoring_an_unlocked_job_is_free(db, trial_on, fake_llm, monkeypatch):
    user = _make_user(db)
    _make_resume(db, user)
    posting = _make_posting(db)
    _score(db, user, posting)
    monkeypatch.setattr(
        resumes_routes,
        "generate_tailored_resume_with_llm",
        lambda *a, **k: TailoredResumeContent(summary="Tailored.", sections=[], contact=None, raw_response={}),
    )
    monkeypatch.setattr(resumes_routes, "upload_file", lambda *a, **k: None)

    tailored = generate_main_tailored_resume(posting.id, resume_id=None, current_user=user, db=db)

    assert tailored.content["summary"] == "Tailored."
    assert user.free_evaluations_used == 1


def test_tailoring_first_unlocks_the_job_too(db, trial_on, monkeypatch):
    user = _make_user(db)
    _make_resume(db, user)
    posting = _make_posting(db)
    monkeypatch.setattr(
        resumes_routes,
        "generate_tailored_resume_with_llm",
        lambda *a, **k: TailoredResumeContent(summary="Tailored.", sections=[], contact=None, raw_response={}),
    )
    monkeypatch.setattr(resumes_routes, "upload_file", lambda *a, **k: None)

    generate_main_tailored_resume(posting.id, resume_id=None, current_user=user, db=db)

    assert user.free_evaluations_used == 1
    assert ai_access.free_trial_job_ids(db, user) == [posting.id]


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


def test_free_trial_counts_as_ai_access_once_the_page_is_seen(db, trial_on):
    # The trial is on for everyone, so it only completes the step after the
    # user has opened AI access — otherwise the step would be skipped.
    user = _make_user(db)
    assert _steps(read_onboarding(current_user=user, db=db))["ai_access"] is False

    result = ai_access_seen(current_user=user, db=db)
    assert _steps(result)["ai_access"] is True
    first_seen = user.ai_access_seen_at
    ai_access_seen(current_user=user, db=db)
    assert user.ai_access_seen_at == first_seen

    user.free_evaluations_used = settings.free_evaluation_limit
    db.commit()
    assert _steps(read_onboarding(current_user=user, db=db))["ai_access"] is False


def test_having_used_a_free_evaluation_counts_as_seen(db, trial_on):
    user = _make_user(db)
    user.free_evaluations_used = 1
    db.commit()

    assert _steps(read_onboarding(current_user=user, db=db))["ai_access"] is True


def test_a_key_completes_ai_access_without_visiting(db, trial_on):
    user = _make_user(db)
    _make_key(db, user)

    assert _steps(read_onboarding(current_user=user, db=db))["ai_access"] is True


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


# ------------------------------------------------------------------ free restructures


def test_free_restructures_are_counted_separately_until_the_limit(db, trial_on, monkeypatch):
    monkeypatch.setattr(settings, "free_restructure_limit", 2)
    user = _make_user(db)

    first = ai_access.structure_llm_credentials(db, user)
    ai_access.structure_llm_credentials(db, user)

    assert first.api_key == "system-key"
    assert user.free_restructures_used == 2
    assert user.free_evaluations_used == 0
    with pytest.raises(HTTPException) as exc_info:
        ai_access.structure_llm_credentials(db, user)
    assert exc_info.value.status_code == 422
    assert "used all 2 free resume restructures" in exc_info.value.detail


def test_restructuring_with_own_key_spends_nothing(db, trial_on):
    user = _make_user(db)
    _make_key(db, user)

    assert ai_access.structure_llm_credentials(db, user).api_key == "own-key"
    assert user.free_restructures_used == 0


def test_free_restructures_off_without_a_system_key(db, trial_off):
    user = _make_user(db)

    with pytest.raises(HTTPException) as exc_info:
        ai_access.structure_llm_credentials(db, user)
    assert exc_info.value.status_code == 422


def test_ai_access_reports_free_restructures(db, trial_on, monkeypatch):
    monkeypatch.setattr(settings, "free_restructure_limit", 5)
    user = _make_user(db)
    ai_access.structure_llm_credentials(db, user)

    summary = read_ai_access(current_user=user, db=db)

    assert summary.free_restructure_limit == 5
    assert summary.free_restructures_remaining == 4


def _upload(db, user, monkeypatch):
    import io

    from fastapi import UploadFile
    from starlette.datastructures import Headers

    from app.api.routes.resumes import upload_resume

    monkeypatch.setattr(resumes_routes, "extract_text", lambda data, content_type: "Python engineer")
    monkeypatch.setattr(resumes_routes, "upload_file", lambda *a, **k: None)
    file = UploadFile(
        file=io.BytesIO(b"pdf bytes"), filename="resume.pdf", headers=Headers({"content-type": "application/pdf"})
    )
    return upload_resume(file=file, current_user=user, db=db)


def test_upload_on_the_free_trial_is_structured_automatically(db, trial_on, monkeypatch):
    monkeypatch.setattr(
        resumes_routes,
        "extract_resume_structure_with_llm",
        lambda *a, **k: TailoredResumeContent(summary="Engineer.", sections=[], contact=None, raw_response={}),
    )
    user = _make_user(db)

    resume = _upload(db, user, monkeypatch)

    assert resume.structured_content["summary"] == "Engineer."
    assert user.free_restructures_used == 1


def test_a_failed_structuring_at_upload_is_not_counted(db, trial_on, monkeypatch):
    def boom(*a, **k):
        raise LlmError("down")

    monkeypatch.setattr(resumes_routes, "extract_resume_structure_with_llm", boom)
    user = _make_user(db)

    resume = _upload(db, user, monkeypatch)

    assert resume.structured_content is None
    db.refresh(user)
    assert user.free_restructures_used == 0
