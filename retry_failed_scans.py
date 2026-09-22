"""Retry sweep: re-publish scan requests for every JobPostingUrl at FAILED
whose backoff window has elapsed (see app.services.jobs.wake_retryable_failed_scans
and its scan_attempts/next_retry_at columns, and scan_retry_* in app.core.config
for the backoff schedule). A row that's exhausted scan_retry_max_attempts sits at
NEEDS_REVIEW instead — a dead end until a human rescans it — and is never picked
up here.

Deployed with its own hourly Cloud Scheduler trigger, separate from
crawl_dispatcher.py's daily discovery cadence — a FAILED row's backoff windows
are as short as an hour, far finer-grained than discovery needs to run.

This script doesn't know or care how it's invoked — point any scheduler at it
(cron, GCP Cloud Scheduler + a Cloud Run Job, GitHub Actions, etc.) with
whatever cadence you want (hourly is the assumption the backoff schedule was
tuned against; see scan_retry_base_seconds's docstring).

Usage: python retry_failed_scans.py
"""

import logging

from app.core.log_config import configure_logging
from app.db.session import SessionLocal
from app.services.job_queue import ensure_topic_and_subscription
from app.services.jobs import wake_retryable_failed_scans, wake_sources_with_pending_scans

configure_logging()
logger = logging.getLogger("app.retry_failed_scans")


def main() -> None:
    ensure_topic_and_subscription()

    db = SessionLocal()
    try:
        retried = wake_retryable_failed_scans(db)
        logger.info("Reset %d failed url(s) back to pending for retry.", retried)

        # Same two-step split one_off/requeue_pending_scans.py uses: user-submitted
        # URLs (no crawl source) were just re-queued directly above;
        # crawl-sourced ones need their source's throttled lanes woken
        # instead.
        woken = wake_sources_with_pending_scans(db)
        logger.info("Woke scan lanes for %d crawl source(s) with pending urls.", woken)
    finally:
        db.close()

    logger.info("Done.")


def dispatch(_request=None) -> tuple[str, int]:
    """Cloud Functions (2nd gen) HTTP entry point — Cloud Scheduler calls this
    on a cron cadence instead of a shell invoking main() directly."""
    main()
    return "ok", 200


if __name__ == "__main__":
    main()
