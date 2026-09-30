import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.rate_limit import RateLimitExceeded
from app.models.feedback import Feedback
from app.models.user import User
from app.schemas.feedback import FeedbackCreate
from app.services.email import send_feedback_notification_email

logger = logging.getLogger("app.feedback")


def _enforce_feedback_rate_limit(db: Session, user_id: uuid.UUID, now: datetime) -> None:
    window_start = now - timedelta(minutes=settings.feedback_rate_limit_window_minutes)
    count = db.scalar(
        select(func.count())
        .select_from(Feedback)
        .where(Feedback.user_id == user_id, Feedback.created_at >= window_start)
    )
    if count >= settings.feedback_rate_limit_max_per_user:
        raise RateLimitExceeded("You've sent a lot of feedback recently. Try again in a little while.")


def create_feedback(
    db: Session,
    user: User,
    payload: FeedbackCreate,
    *,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> Feedback:
    """Store a feedback/support submission and email every admin about it.

    The email is best-effort: the submission is already saved, so a failed
    send is logged rather than surfaced — the admin page is the source of
    truth, the email is just a nudge to go look at it. `now` is only for
    tests.
    """
    now = now or datetime.now(timezone.utc)
    _enforce_feedback_rate_limit(db, user.id, now)

    feedback = Feedback(
        user_id=user.id,
        kind=payload.kind,
        message=payload.message.strip(),
        rating=payload.rating,
        page_url=payload.page_url,
        user_agent=(user_agent or "")[:512] or None,
    )
    db.add(feedback)
    db.flush()

    admin_emails = sorted(settings.admin_email_set)
    if admin_emails:
        try:
            send_feedback_notification_email(
                admin_emails,
                from_user_email=user.email,
                kind=feedback.kind,
                message=feedback.message,
                page_url=feedback.page_url,
            )
        except Exception:
            logger.exception("Feedback %s saved but the admin notification failed", feedback.id)
    return feedback
