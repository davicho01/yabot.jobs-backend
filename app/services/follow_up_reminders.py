"""Emails a reminder for every application whose follow-up date is due — see
follow_up_reminders.py at the repo root for the scheduled entrypoint that
calls send_due_reminders on a cadence.
"""

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.enums import ApplicationStatus
from app.models.job_application import UserJobApplication
from app.services.email import send_follow_up_reminder_email

logger = logging.getLogger("app.follow_up_reminders")

# Nothing left to follow up on once an application has landed here, even if
# a reminder date was set while it was still active.
_TERMINAL_STATUSES = (ApplicationStatus.REJECTED, ApplicationStatus.WITHDRAWN)


def send_due_reminders(db: Session, today: date | None = None) -> int:
    """Emails a reminder for every application with a follow_up_at on or
    before today that hasn't been reminded about yet — follow_up_reminded_at
    is null, which update_application resets to null whenever follow_up_at
    itself changes, so a reminder is sent exactly once per date set. Skips
    archived applications and ones with a terminal status (rejected/
    withdrawn) — a follow-up doesn't mean anything on those, even if the
    date predates the status change. Returns how many reminders were sent.
    """
    today = today or datetime.now(timezone.utc).date()
    sent = 0
    applications = db.scalars(
        select(UserJobApplication)
        .where(
            UserJobApplication.follow_up_at.is_not(None),
            UserJobApplication.follow_up_at <= today,
            UserJobApplication.follow_up_reminded_at.is_(None),
            UserJobApplication.is_archived.is_(False),
            UserJobApplication.status.not_in(_TERMINAL_STATUSES),
        )
        .options(selectinload(UserJobApplication.user), selectinload(UserJobApplication.job_posting))
    ).all()
    logger.info("Found %d due follow-up reminder(s).", len(applications))
    for application in applications:
        try:
            _send_one(application)
            application.follow_up_reminded_at = datetime.now(timezone.utc)
            sent += 1
        except Exception:
            logger.exception("Follow-up reminder failed for application %s; skipping.", application.id)
    return sent


def _send_one(application: UserJobApplication) -> None:
    posting = application.job_posting
    send_follow_up_reminder_email(
        application.user.email,
        title=posting.title,
        company_name=posting.company_name,
        apply_url=f"{settings.frontend_base_url}/jobs/{posting.url_id}/apply",
    )
