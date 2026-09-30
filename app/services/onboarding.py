from datetime import datetime, timezone

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.job_application import UserJobApplication
from app.models.resume import Resume, ResumeScore
from app.models.user import User
from app.schemas.onboarding import AiAccessRead, OnboardingRead, OnboardingStepRead
from app.services.ai_access import (
    active_source,
    free_evaluations_remaining,
    free_restructures_enabled,
    free_restructures_remaining,
    free_trial_enabled,
    free_trial_job_ids,
    has_active_subscription,
    subscription_requests_used,
    subscriptions_enabled,
)


def get_ai_access(db: Session, user: User) -> AiAccessRead:
    source, key = active_source(db, user)
    return AiAccessRead(
        has_own_key=key is not None,
        active_source=source,
        own_key_provider=key.provider if key else None,
        own_key_model=key.model if key else None,
        free_trial_enabled=free_trial_enabled(),
        free_evaluation_limit=settings.free_evaluation_limit,
        free_evaluations_used=user.free_evaluations_used,
        free_evaluations_remaining=free_evaluations_remaining(user),
        free_trial_job_ids=free_trial_job_ids(db, user),
        free_restructure_limit=settings.free_restructure_limit if free_restructures_enabled() else 0,
        free_restructures_remaining=free_restructures_remaining(user),
        subscription_available=subscriptions_enabled(),
        subscription_price_label=settings.subscription_price_label,
        subscribed=has_active_subscription(user),
        subscription_status=user.subscription_status,
        subscription_current_period_end=user.subscription_current_period_end,
        subscription_cancel_at_period_end=user.subscription_cancel_at_period_end,
        subscription_requests_used=subscription_requests_used(user),
        subscription_request_limit=settings.subscription_monthly_request_limit,
    )


def _ai_access_step_done(user: User, ai_access: AiAccessRead) -> bool:
    """The "Set up AI access" step. A key or the plan completes it. The free
    trial only does once the user has seen the AI access page (or already
    used a free evaluation): it's on for everyone, so it would otherwise tick
    this step off before they ever saw their options."""
    if ai_access.has_own_key or ai_access.subscribed:
        return True
    seen = user.ai_access_seen_at is not None or user.free_evaluations_used > 0
    return seen and ai_access.free_evaluations_remaining > 0


def mark_ai_access_seen(db: Session, user: User, *, now: datetime | None = None) -> OnboardingRead:
    if user.ai_access_seen_at is None:
        user.ai_access_seen_at = now or datetime.now(timezone.utc)
        db.flush()
    return get_onboarding(db, user, now=now)


def get_onboarding(db: Session, user: User, *, now: datetime | None = None) -> OnboardingRead:
    """The getting-started checklist: upload a resume, set up AI access (own
    key, subscription, or free trial), start an application, get a first
    evaluation.

    Each step is derived from the user's actual data rather than stored, so
    it's right on every device and no route has to remember to tick it off.
    The first time every step is done, completed_at is stamped so the
    checklist stays finished even if a step later un-completes (e.g. the
    free trial runs out — that's the AI-access prompt's job, not this one's).
    """
    ai_access = get_ai_access(db, user)
    steps = [
        OnboardingStepRead(
            key="resume", done=bool(db.scalar(select(exists().where(Resume.user_id == user.id))))
        ),
        OnboardingStepRead(key="ai_access", done=_ai_access_step_done(user, ai_access)),
        OnboardingStepRead(
            key="application",
            done=bool(db.scalar(select(exists().where(UserJobApplication.user_id == user.id)))),
        ),
        OnboardingStepRead(
            key="evaluation", done=bool(db.scalar(select(exists().where(ResumeScore.user_id == user.id))))
        ),
    ]
    if user.onboarding_completed_at is None and all(step.done for step in steps):
        user.onboarding_completed_at = now or datetime.now(timezone.utc)
        db.flush()
    return OnboardingRead(
        steps=steps,
        completed_at=user.onboarding_completed_at,
        dismissed_at=user.onboarding_dismissed_at,
        ai_access=ai_access,
    )


def dismiss_onboarding(db: Session, user: User, *, now: datetime | None = None) -> OnboardingRead:
    if user.onboarding_dismissed_at is None:
        user.onboarding_dismissed_at = now or datetime.now(timezone.utc)
        db.flush()
    return get_onboarding(db, user, now=now)
