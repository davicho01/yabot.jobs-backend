from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class AiAccessRead(BaseModel):
    """How the user's resume evaluations get paid for — their own key, the
    free trial, or neither yet. See app.services.ai_access."""

    has_own_key: bool
    free_trial_enabled: bool
    free_evaluation_limit: int
    free_evaluations_used: int
    free_evaluations_remaining: int


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
