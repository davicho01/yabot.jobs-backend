"""Who pays for a user's resume-evaluation LLM calls.

Resume features are bring-your-own-key (see
app.services.resume_llm.get_users_default_llm_key). The one exception is
the free trial: a user with no key of their own gets
`settings.free_evaluation_limit` job evaluations on the system-wide key
(SYSTEM_LLM_*), so they can see what the app does before being asked for
a key. A "free evaluation" is one POST /resumes/main/score for a job, plus
that score's first POST /resumes/main/evaluation breakdown at no extra
cost — together they're what the Apply page calls a fit check.
"""

import uuid
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.api_key import UserApiKey
from app.models.enums import LlmProvider
from app.models.user import User

NO_KEY_MESSAGE = "Add an AI API key in AI API Keys to use this feature."


def trial_exhausted_message() -> str:
    return (
        f"You've used all {settings.free_evaluation_limit} free evaluations. "
        "Add your own AI API key in AI API Keys to keep going."
    )


@dataclass(frozen=True)
class LlmCredentials:
    provider: str
    model: str | None
    api_key: str
    base_url: str | None
    # True when this call runs on the system key under the free trial
    # rather than on the user's own key.
    is_free_trial: bool


def free_trial_enabled() -> bool:
    """The trial needs a working system key to run on — without one (or with
    the limit set to 0) the app is purely bring-your-own-key, same as before
    the trial existed."""
    if settings.free_evaluation_limit <= 0:
        return False
    if not settings.system_llm_provider or not settings.system_llm_api_key:
        return False
    # Anthropic is the only provider with a default model (see llm_client).
    return bool(settings.system_llm_model) or settings.system_llm_provider == LlmProvider.ANTHROPIC


def free_evaluations_remaining(user: User) -> int:
    if not free_trial_enabled():
        return 0
    return max(0, settings.free_evaluation_limit - user.free_evaluations_used)


def get_own_default_key(db: Session, user_id: uuid.UUID) -> UserApiKey | None:
    return db.scalar(
        select(UserApiKey).where(
            UserApiKey.user_id == user_id, UserApiKey.is_default.is_(True), UserApiKey.is_active.is_(True)
        )
    )


def _system_credentials() -> LlmCredentials:
    return LlmCredentials(
        provider=settings.system_llm_provider,
        model=settings.system_llm_model,
        api_key=settings.system_llm_api_key,
        base_url=settings.system_llm_base_url,
        is_free_trial=True,
    )


def _no_access_error() -> HTTPException:
    detail = trial_exhausted_message() if free_trial_enabled() else NO_KEY_MESSAGE
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def _own_credentials(key: UserApiKey) -> LlmCredentials:
    return LlmCredentials(
        provider=key.provider,
        model=key.model,
        api_key=key.get_plaintext_key(),
        base_url=key.base_url,
        is_free_trial=False,
    )


def use_free_evaluation(db: Session, user: User) -> LlmCredentials:
    """Credentials for a new job score (POST /resumes/main/score): the user's
    own key if they have one, otherwise one of their free evaluations.

    The free evaluation is taken up front with a conditional UPDATE, so two
    concurrent requests can't both spend the last one. The route's
    transaction rolls back if the LLM call then fails (see
    app.db.session.get_db), which gives the evaluation back — a failed call
    never costs the user anything.
    """
    key = get_own_default_key(db, user.id)
    if key is not None:
        return _own_credentials(key)
    if not free_trial_enabled():
        raise _no_access_error()

    result = db.execute(
        update(User)
        .where(User.id == user.id, User.free_evaluations_used < settings.free_evaluation_limit)
        .values(free_evaluations_used=User.free_evaluations_used + 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        raise _no_access_error()
    db.refresh(user, attribute_names=["free_evaluations_used"])
    return _system_credentials()


def evaluation_breakdown_credentials(
    db: Session, user: User, *, score_was_free_trial: bool, already_evaluated: bool
) -> LlmCredentials:
    """Credentials for a score's detailed breakdown (POST
    /resumes/main/evaluation). The first breakdown of a score that was itself
    a free evaluation comes with it; anything else (re-running a breakdown,
    or breaking down a score made with a key that's since been removed)
    needs the user's own key.
    """
    key = get_own_default_key(db, user.id)
    if key is not None:
        return _own_credentials(key)
    if already_evaluated or not score_was_free_trial or not free_trial_enabled():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=NO_KEY_MESSAGE)
    return _system_credentials()
