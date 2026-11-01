from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.rate_limit import RateLimitExceeded
from app.models.survey_response import SurveyResponse
from app.models.user import User
from app.schemas.survey import SurveySubmit

_SINGLE_ANSWERS = ("source", "search_duration", "verification_dates_rating", "dedup_rating")
_MULTI_ANSWERS = ("industries", "work_arrangement", "liked", "wanted")


def save_survey_answers(
    db: Session,
    user: User | None,
    payload: SurveySubmit,
    *,
    now: datetime | None = None,
) -> SurveyResponse:
    """Create or update the visitor's row with the answers on this page.

    Only the fields the client sent are written (page 1 then page 2 each send
    their own), and a row is keyed on the browser's visitor_id, so re-posting
    a page edits it rather than adding a second response. Only creating a
    *new* row is rate limited: an overall cap per window, since signed-out
    visitors have nothing else to key a limit on. `now` is only for tests.
    """
    now = now or datetime.now(timezone.utc)
    response = db.scalar(select(SurveyResponse).where(SurveyResponse.visitor_id == payload.visitor_id))
    if response is None:
        _enforce_new_response_limit(db, now)
        response = SurveyResponse(visitor_id=payload.visitor_id)
        db.add(response)

    answers = payload.model_dump(exclude_unset=True, exclude={"visitor_id", "completed"})
    for field, value in answers.items():
        if isinstance(value, str):
            value = value.strip() or None
        setattr(response, field, value)
    if user is not None:
        response.user_id = user.id
        # The account email is already known; a typed one would just be stale.
        # Whether they're happy to be contacted is the checkbox on page 2.
        response.contact_email = None
    else:
        # Signed out: leaving an email is the consent, there is no checkbox.
        response.contact_ok = response.contact_email is not None
    # Once finished, a stray late page-1 post can't un-finish it.
    response.completed = response.completed or payload.completed
    db.flush()
    return response


def _enforce_new_response_limit(db: Session, now: datetime) -> None:
    window_start = now - timedelta(minutes=settings.survey_rate_limit_window_minutes)
    count = db.scalar(
        select(func.count()).select_from(SurveyResponse).where(SurveyResponse.created_at >= window_start)
    )
    if count >= settings.survey_rate_limit_max_new:
        raise RateLimitExceeded("The survey is getting a lot of traffic right now. Please try again in a little while.")


def answer_counts(db: Session) -> dict[str, dict[str, int]]:
    """How many responses gave each answer, per question — computed in Python
    because the multi-select answers are JSON lists. A survey is at most a few
    thousand rows, so one pass over the answer columns is fine."""
    counts: dict[str, Counter[str]] = {name: Counter() for name in (*_SINGLE_ANSWERS, *_MULTI_ANSWERS)}
    rows = db.execute(
        select(*(getattr(SurveyResponse, name) for name in (*_SINGLE_ANSWERS, *_MULTI_ANSWERS)))
    ).all()
    for row in rows:
        for name, value in zip((*_SINGLE_ANSWERS, *_MULTI_ANSWERS), row):
            if value is None:
                continue
            counts[name].update(value if isinstance(value, list) else [value])
    return {name: dict(counter) for name, counter in counts.items()}
