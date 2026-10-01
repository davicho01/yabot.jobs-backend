"""Who pays for a user's resume LLM calls (scoring, tailoring, cover letters…).

In order of preference:

1. A paid subscription (see app.services.billing): resume-wide features
   (structuring, reviewing) run on the system-wide key (SYSTEM_LLM_*), with
   an optional `settings.subscription_monthly_request_limit` per billing
   period. Job-scoped features (score, breakdown, tailored resume, cover
   letter, interview prep) instead meter by distinct job: each subscriber
   has a per-user `User.subscription_evaluation_limit` (100 by default,
   room for a future higher-priced tier to grant more) of jobs they can
   unlock per billing period, the same "first touch unlocks the job, the
   rest is free" shape as the free trial below (see job_llm_credentials and
   _unlock_subscription_job). The subscription wins over a saved key on
   purpose: someone paying for the plan must never also be billed by their
   own provider without realizing it. Saved keys stay put and take over
   again once the plan ends.
2. The user's own default API key (bring-your-own-key) — unlimited, and
   billed by their provider, not us.
3. The free trial: `settings.free_evaluation_limit` jobs on the system
   key, so a new user can see the whole product before being asked for a
   key or a subscription. The first AI request for a job (whichever feature
   it is) unlocks that job and spends one free evaluation; every AI feature
   for that job is then free — score, breakdown, tailored resume, cover
   letter, interview prep (see job_llm_credentials). Features that aren't
   about one job (e.g. structuring or reviewing a resume) aren't covered.
   Unlike the subscription's job cap, this one is a lifetime total, not a
   per-period one.

Metered use (a subscription request, a subscription job unlock, or a free
evaluation) is taken up front with a conditional UPDATE, so concurrent
requests can't overspend it. The route's transaction rolls back if the LLM
call then fails (see app.db.session.get_db), which gives it back — a failed
call never costs the user anything.
"""

import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import case, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.api_key import UserApiKey
from app.models.enums import LlmProvider
from app.models.free_trial_job import FreeTrialJob
from app.models.subscription_evaluation_job import SubscriptionEvaluationJob
from app.models.user import User

# Stripe subscription statuses that grant access. past_due is deliberately
# left out: Stripe keeps retrying the card, and access comes back the moment
# a retry succeeds and the subscription returns to active.
ACTIVE_SUBSCRIPTION_STATUSES = frozenset({"active", "trialing"})

CredentialSource = Literal["own_key", "subscription", "free_trial"]


@dataclass(frozen=True)
class LlmCredentials:
    provider: str
    model: str | None
    api_key: str
    base_url: str | None
    source: CredentialSource

    @property
    def is_free_trial(self) -> bool:
        return self.source == "free_trial"


def system_llm_configured() -> bool:
    if not settings.system_llm_provider or not settings.system_llm_api_key:
        return False
    # Anthropic is the only provider with a default model (see llm_client).
    return bool(settings.system_llm_model) or settings.system_llm_provider == LlmProvider.ANTHROPIC


def free_trial_enabled() -> bool:
    """The trial needs a working system key to run on — without one (or with
    the limit set to 0) the app is purely bring-your-own-key."""
    return settings.free_evaluation_limit > 0 and system_llm_configured()


def subscriptions_enabled() -> bool:
    """Whether new subscriptions can be sold: Stripe is configured and there's
    a system key for subscribers' requests to run on."""
    return bool(settings.stripe_secret_key and settings.stripe_price_id) and system_llm_configured()


def has_active_subscription(user: User) -> bool:
    # Doesn't check subscriptions_enabled(): someone already paying keeps
    # access even if new sign-ups are switched off. It does need the system
    # key, which is what their requests run on.
    return user.subscription_status in ACTIVE_SUBSCRIPTION_STATUSES and system_llm_configured()


def free_restructures_enabled() -> bool:
    return settings.free_restructure_limit > 0 and system_llm_configured()


def free_restructures_remaining(user: User) -> int:
    if not free_restructures_enabled():
        return 0
    return max(0, settings.free_restructure_limit - user.free_restructures_used)


def free_evaluations_remaining(user: User) -> int:
    if not free_trial_enabled():
        return 0
    return max(0, settings.free_evaluation_limit - user.free_evaluations_used)


def subscription_requests_used(user: User) -> int:
    """This billing period's count — a counter left over from a previous
    period reads as 0 (it's reset lazily on the next request)."""
    if user.subscription_usage_period_end != user.subscription_current_period_end:
        return 0
    return user.subscription_usage_count


def get_own_default_key(db: Session, user_id: uuid.UUID) -> UserApiKey | None:
    return db.scalar(
        select(UserApiKey).where(
            UserApiKey.user_id == user_id, UserApiKey.is_default.is_(True), UserApiKey.is_active.is_(True)
        )
    )


def subscription_evaluations_used(user: User) -> int:
    """This billing period's distinct-job-unlock count — a counter left over
    from a previous period reads as 0 (it's reset lazily on the next unlock),
    same shape as subscription_requests_used."""
    if user.subscription_evaluations_period_end != user.subscription_current_period_end:
        return 0
    return user.subscription_evaluations_used


def _own_credentials(key: UserApiKey) -> LlmCredentials:
    return LlmCredentials(
        provider=key.provider,
        model=key.model,
        api_key=key.get_plaintext_key(),
        base_url=key.base_url,
        source="own_key",
    )


def _system_credentials(source: CredentialSource) -> LlmCredentials:
    return LlmCredentials(
        provider=settings.system_llm_provider,
        model=settings.system_llm_model,
        api_key=settings.system_llm_api_key,
        base_url=settings.system_llm_base_url,
        source=source,
    )


def no_access_message() -> str:
    if subscriptions_enabled():
        return (
            f"Add an AI API key in AI access, or subscribe for {settings.subscription_price_label}, "
            "to use this feature."
        )
    return "Add an AI API key in AI access to use this feature."


def trial_exhausted_message() -> str:
    if subscriptions_enabled():
        return (
            f"You've used all {settings.free_evaluation_limit} free evaluations. Add your own AI API key in "
            f"AI access, or subscribe for {settings.subscription_price_label}, to keep going."
        )
    return (
        f"You've used all {settings.free_evaluation_limit} free evaluations. "
        "Add your own AI API key in AI access to keep going."
    )


def active_source(db: Session, user: User) -> tuple[CredentialSource | None, UserApiKey | None]:
    """What the user's next AI request would run on, and their default key
    (returned even when the plan outranks it, so the UI can say it's unused).
    Same order as the resolvers below, but read-only: nothing is metered.
    "free_trial" means free evaluations are left for new jobs; the trial
    covers job features only, not resume-wide ones."""
    key = get_own_default_key(db, user.id)
    if has_active_subscription(user):
        return "subscription", key
    if key is not None:
        return "own_key", key
    if free_evaluations_remaining(user) > 0:
        return "free_trial", None
    return None, None


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def _use_subscription_request(db: Session, user: User) -> LlmCredentials:
    """Count one request against the current billing period, resetting the
    counter when the period has rolled over since the last one."""
    period_end = user.subscription_current_period_end
    limit = settings.subscription_monthly_request_limit
    new_period = User.subscription_usage_period_end.is_distinct_from(period_end)
    stmt = (
        update(User)
        .where(User.id == user.id)
        .values(
            subscription_usage_count=case((new_period, 1), else_=User.subscription_usage_count + 1),
            subscription_usage_period_end=period_end,
        )
        .execution_options(synchronize_session=False)
    )
    if limit > 0:
        stmt = stmt.where(or_(new_period, User.subscription_usage_count < limit))
    if db.execute(stmt).rowcount == 0:
        renews = f" on {period_end:%B} {period_end.day}" if period_end else " when your plan renews"
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"You've used this month's {limit} AI requests. They reset{renews}. "
                "You can also add your own AI API key in AI access for unlimited use."
            ),
        )
    db.refresh(user, attribute_names=["subscription_usage_count", "subscription_usage_period_end"])
    return _system_credentials("subscription")


def resolve_llm_credentials(db: Session, user: User) -> LlmCredentials:
    """Credentials for a resume LLM feature that isn't about one job (e.g.
    structuring or reviewing the resume): the user's subscription, else
    their own key. The free trial doesn't cover these — job features go
    through job_llm_credentials instead."""
    if has_active_subscription(user):
        return _use_subscription_request(db, user)
    key = get_own_default_key(db, user.id)
    if key is not None:
        return _own_credentials(key)
    raise _unprocessable(no_access_message())


def _is_unlocked(db: Session, user: User, job_posting_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(FreeTrialJob.id).where(
                FreeTrialJob.user_id == user.id, FreeTrialJob.job_posting_id == job_posting_id
            )
        )
        is not None
    )


def _unlock_job(db: Session, user: User, job_posting_id: uuid.UUID) -> None:
    """Spend one free evaluation on this job. The row goes in first, inside a
    savepoint: if a concurrent request (say, a double click) is unlocking the
    same job, the unique constraint makes this one wait and then find it
    already unlocked, rather than spending a second evaluation on it."""
    try:
        with db.begin_nested():
            db.add(FreeTrialJob(user_id=user.id, job_posting_id=job_posting_id))
    except IntegrityError:
        return
    spent = db.execute(
        update(User)
        .where(User.id == user.id, User.free_evaluations_used < settings.free_evaluation_limit)
        .values(free_evaluations_used=User.free_evaluations_used + 1)
        .execution_options(synchronize_session=False)
    ).rowcount
    if spent == 0:
        # The route's rollback takes the FreeTrialJob row back out too.
        raise _unprocessable(trial_exhausted_message())
    db.refresh(user, attribute_names=["free_evaluations_used"])


def _is_subscription_job_unlocked(db: Session, user: User, job_posting_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(SubscriptionEvaluationJob.id).where(
                SubscriptionEvaluationJob.user_id == user.id,
                SubscriptionEvaluationJob.job_posting_id == job_posting_id,
                SubscriptionEvaluationJob.period_end.is_(user.subscription_current_period_end)
                if user.subscription_current_period_end is None
                else SubscriptionEvaluationJob.period_end == user.subscription_current_period_end,
            )
        )
        is not None
    )


def _unlock_subscription_job(db: Session, user: User, job_posting_id: uuid.UUID) -> None:
    """Spend one of this period's evaluations on this job. The row goes in
    first, inside a savepoint: if a concurrent request is unlocking the same
    job in the same period, the unique constraint makes this one wait and
    then find it already unlocked, rather than spending a second evaluation
    on it (mirrors _unlock_job)."""
    period_end = user.subscription_current_period_end
    try:
        with db.begin_nested():
            db.add(SubscriptionEvaluationJob(user_id=user.id, job_posting_id=job_posting_id, period_end=period_end))
    except IntegrityError:
        return
    limit = user.subscription_evaluation_limit
    new_period = User.subscription_evaluations_period_end.is_distinct_from(period_end)
    stmt = (
        update(User)
        .where(User.id == user.id)
        .values(
            subscription_evaluations_used=case((new_period, 1), else_=User.subscription_evaluations_used + 1),
            subscription_evaluations_period_end=period_end,
        )
        .execution_options(synchronize_session=False)
    )
    if limit > 0:
        stmt = stmt.where(or_(new_period, User.subscription_evaluations_used < limit))
    if db.execute(stmt).rowcount == 0:
        # The route's rollback takes the SubscriptionEvaluationJob row back out too.
        renews = f" on {period_end:%B} {period_end.day}" if period_end else " when your plan renews"
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"You've used this month's {limit} AI evaluations. They reset{renews}. "
                "You can also add your own AI API key in AI access for unlimited use."
            ),
        )
    db.refresh(user, attribute_names=["subscription_evaluations_used", "subscription_evaluations_period_end"])


def job_llm_credentials(db: Session, user: User, job_posting_id: uuid.UUID) -> LlmCredentials:
    """Credentials for an AI feature about one job — its score or breakdown,
    tailored resume (and that version's score), cover letter, interview prep.
    Subscription (capped per period by distinct job, see
    _unlock_subscription_job), else own key, else the free trial: free if
    this job is already unlocked, otherwise unlocking it spends one free
    evaluation."""
    if has_active_subscription(user):
        if not _is_subscription_job_unlocked(db, user, job_posting_id):
            _unlock_subscription_job(db, user, job_posting_id)
        return _system_credentials("subscription")
    key = get_own_default_key(db, user.id)
    if key is not None:
        return _own_credentials(key)
    if not free_trial_enabled():
        raise _unprocessable(no_access_message())
    if not _is_unlocked(db, user, job_posting_id):
        _unlock_job(db, user, job_posting_id)
    return _system_credentials("free_trial")


def free_trial_job_ids(db: Session, user: User) -> list[uuid.UUID]:
    return list(db.scalars(select(FreeTrialJob.job_posting_id).where(FreeTrialJob.user_id == user.id)).all())


def structure_llm_credentials(db: Session, user: User) -> LlmCredentials:
    """Credentials for structuring a resume (turning it into editable
    sections — on upload, or POST /resumes/{id}/structure). Plan, else own
    key, else one of the user's `free_restructure_limit` free restructures,
    taken with a conditional UPDATE like the other metered uses (and handed
    back by the route's rollback if the LLM call fails)."""
    if has_active_subscription(user):
        return _use_subscription_request(db, user)
    key = get_own_default_key(db, user.id)
    if key is not None:
        return _own_credentials(key)
    if not free_restructures_enabled():
        raise _unprocessable(no_access_message())
    spent = db.execute(
        update(User)
        .where(User.id == user.id, User.free_restructures_used < settings.free_restructure_limit)
        .values(free_restructures_used=User.free_restructures_used + 1)
        .execution_options(synchronize_session=False)
    ).rowcount
    if spent == 0:
        raise _unprocessable(
            f"You've used all {settings.free_restructure_limit} free resume restructures. "
            + no_access_message()
        )
    db.refresh(user, attribute_names=["free_restructures_used"])
    return _system_credentials("free_trial")
