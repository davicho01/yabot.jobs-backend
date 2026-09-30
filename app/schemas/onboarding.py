from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class AiAccessRead(BaseModel):
    """How the user's resume AI requests get paid for — their own key, a
    subscription, the free trial, or nothing yet. See app.services.ai_access."""

    has_own_key: bool
    free_trial_enabled: bool
    free_evaluation_limit: int
    free_evaluations_used: int
    free_evaluations_remaining: int
    # The paid plan (see app.services.billing). subscription_status is
    # Stripe's own value, null if the user never subscribed.
    subscription_available: bool
    subscription_price_label: str
    subscribed: bool
    subscription_status: str | None
    subscription_current_period_end: datetime | None
    subscription_cancel_at_period_end: bool
    subscription_requests_used: int
    # 0 means unlimited.
    subscription_request_limit: int


OnboardingStepKey = Literal["resume", "ai_access", "application", "evaluation"]


class OnboardingStepRead(BaseModel):
    key: OnboardingStepKey
    done: bool


class OnboardingRead(BaseModel):
    # In the order the user should do them.
    steps: list[OnboardingStepRead]
    completed_at: datetime | None
    dismissed_at: datetime | None
    ai_access: AiAccessRead
