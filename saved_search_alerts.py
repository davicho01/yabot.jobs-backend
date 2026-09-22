"""One-shot sweep of every saved search, emailing a digest for whichever
ones have new matches since they were last checked — see
app.services.saved_search_alerts.sweep_saved_searches for the actual logic.

This script doesn't know or care how it's invoked — point any scheduler at
it (cron, GCP Cloud Scheduler + a Cloud Run Job, GitHub Actions, etc.) with
whatever cadence you want (e.g. hourly; new postings show up continuously
via scanning, so unlike the once-a-day discovery crawl there's no natural
"once a day is enough" cadence here — pick whatever email volume feels right).

Usage: python saved_search_alerts.py
"""

import logging

from app.core.log_config import configure_logging
from app.db.session import SessionLocal
from app.services.saved_search_alerts import sweep_saved_searches

configure_logging()
logger = logging.getLogger("app.saved_search_alerts_script")


def main() -> None:
    db = SessionLocal()
    try:
        sent = sweep_saved_searches(db)
        db.commit()
        logger.info("Saved-search sweep sent %d digest(s).", sent)
    finally:
        db.close()


def dispatch(_request=None) -> tuple[str, int]:
    """Cloud Functions (2nd gen) HTTP entry point — Cloud Scheduler calls this
    on a cron cadence instead of a shell invoking main() directly."""
    main()
    return "ok", 200


if __name__ == "__main__":
    main()
