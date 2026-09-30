"""Who pays for a user's resume LLM calls (scoring, tailoring, cover letters…).

In order of preference:

1. A paid subscription (see app.services.billing): every resume feature
   runs on the system-wide key (SYSTEM_LLM_*), with an optional
   `settings.subscription_monthly_request_limit` per billing period. It
   wins over a saved key on purpose: someone paying for the plan must never
   also be billed by their own provider without realizing it. Saved keys
   stay put and take over again once the plan ends.
2. The user's own default API key (bring-your-own-key) — unlimited, and
   billed by their provider, not us.
3. The free trial: `settings.free_evaluation_limit` job evaluations on the
   system key, so a new user can see what the app does before being asked
   for a key or a subscription. A "free evaluation" is one POST
   /resumes/main/score for a job, plus that score's first POST
   /resumes/main/evaluation breakdown at no extra cost — together they're
   what the Apply page calls a fit check. Nothing else is covered by it.

Metered use (a subscription request or a free evaluation) is taken up front
with a conditional UPDATE, so concurrent requests can't overspend it. The
route's transaction rolls back if the LLM call then fails (see
app.db.session.get_db), which gives it back — a failed call never costs the
user anything.
"""

import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import case, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.api_key import UserApiKey
from app.models.enums import LlmProvider
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
            f"Add an AI API key in AI API Keys, or subscribe for {settings.subscription_price_label}, "
            "to use this feature."
        )
    return "Add an AI API key in AI API Keys to use this feature."


def trial_exhausted_message() -> str:
    if subscriptions_enabled():
        return (
            f"You've used all {settings.free_evaluation_limit} free evaluations. Add your own AI API key in "
            f"AI API Keys, or subscribe for {settings.subscription_price_label}, to keep going."
        )
    return (
        f"You've used all {settings.free_evaluation_limit} free evaluations. "
        "Add your own AI API key in AI API Keys to keep going."
    )


def active_source(db: Session, user: User) -> tuple[CredentialSource | None, UserApiKey | None]:
    """What the user's next AI request would run on, and their default key
    (returned even when the plan outranks it, so the UI can say it's unused).
    Same order as the resolvers below, but read-only: nothing is metered.
    "free_trial" only means free evaluations are left — the trial covers job
    scoring alone, not every feature."""
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
                "You can also add your own AI API key in AI API Keys for unlimited use."
            ),
        )
    db.refresh(user, attribute_names=["subscription_usage_count", "subscription_usage_period_end"])
    return _system_credentials("subscription")


def resolve_llm_credentials(db: Session, user: User) -> LlmCredentials:
    """Credentials for any resume LLM feature: the user's subscription, else
    their own key. The free trial doesn't cover these — see
    use_free_evaluation for the one feature it does."""
    if has_active_subscription(user):
        return _use_subscription_request(db, user)
    key = get_own_default_key(db, user.id)
    if key is not None:
        return _own_credentials(key)
    raise _unprocessable(no_access_message())


def use_free_evaluation(db: Session, user: User) -> LlmCredentials:
    """Credentials for a new job score (POST /resumes/main/score):
    subscription, else own key, else one of the user's free evaluations."""
    if has_active_subscription(user):
        return _use_subscription_request(db, user)
    key = get_own_default_key(db, user.id)
    if key is not None:
        return _own_credentials(key)
    if not free_trial_enabled():
        raise _unprocessable(no_access_message())

    result = db.execute(
        update(User)
        .where(User.id == user.id, User.free_evaluations_used < settings.free_evaluation_limit)
        .values(free_evaluations_used=User.free_evaluations_used + 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        raise _unprocessable(trial_exhausted_message())
    db.refresh(user, attribute_names=["free_evaluations_used"])
    return _system_credentials("free_trial")


def evaluation_breakdown_credentials(
    db: Session, user: User, *, score_was_free_trial: bool, already_evaluated: bool
) -> LlmCredentials:
    """Credentials for a score's detailed breakdown (POST
    /resumes/main/evaluation). The first breakdown of a score that was itself
    a free evaluation comes with it; anything else (re-running a breakdown,
    or breaking down a score made with a key that's since been removed)
    needs the user's own key or a subscription.
    """
    if has_active_subscription(user):
        return _use_subscription_request(db, user)
    key = get_own_default_key(db, user.id)
    if key is not None:
        return _own_credentials(key)
    if already_evaluated or not score_was_free_trial or not free_trial_enabled():
        raise _unprocessable(no_access_message())
    return _system_credentials("free_trial")
