"""One-shot sweep of every application, emailing a reminder for whichever
ones have a due follow-up date — see
app.services.follow_up_reminders.send_due_reminders for the actual logic.

This script doesn't know or care how it's invoked — point any scheduler at
it (cron, GCP Cloud Scheduler + a Cloud Run Job, GitHub Actions, etc.).
Daily is enough (a follow-up date is a day, not a moment — see
UserJobApplication.follow_up_at), unlike saved_search_alerts.py, which
checks more often since new postings show up continuously.

Usage: python follow_up_reminders.py
"""

import logging

from app.core.log_config import configure_logging
from app.db.session import SessionLocal
from app.services.follow_up_reminders import send_due_reminders

configure_logging()
logger = logging.getLogger("app.follow_up_reminders_script")


def main() -> None:
    db = SessionLocal()
    try:
        sent = send_due_reminders(db)
        db.commit()
        logger.info("Follow-up sweep sent %d reminder(s).", sent)
    finally:
        db.close()


def dispatch(_request=None) -> tuple[str, int]:
    """Cloud Functions (2nd gen) HTTP entry point — Cloud Scheduler calls this
    on a cron cadence instead of a shell invoking main() directly."""
    main()
    return "ok", 200


if __name__ == "__main__":
    main()
