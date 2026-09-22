"""One-shot recovery: re-publish scan requests for every JobPostingUrl still
stuck at "pending" — per URL for user submissions, and one wake-up per allowed
lane for each crawl source that still has pending URLs (crawl-sourced URLs are
scanned through per-source throttled lanes, see app.services.scan_claims).

A user-submitted URL is only ever queued once, at creation time (see
app.services.jobs.get_or_create_job_posting) — if that publish is lost
(the local Pub/Sub emulator has no durability guarantee under heavy publish
bursts, unlike real GCP Pub/Sub) the row is stranded at "pending" forever
with nothing left to redeliver it. Safe to run anytime: process_scan_job is
idempotent (it checks for an existing JobPosting before doing any work), so
re-enqueueing a URL that's already mid-processing or already done is a
no-op, not a duplicate.

Usage: python -m one_off.requeue_pending_scans
"""

import logging

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.enums import ScanStatus
from app.models.job_url import JobPostingUrl
from app.services.job_queue import enqueue_scan, ensure_topic_and_subscription
from app.services.jobs import wake_sources_with_pending_scans

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.requeue_pending_scans")


def main() -> None:
    ensure_topic_and_subscription()

    db = SessionLocal()
    try:
        # User-submitted URLs (no crawl source) are scanned one message per
        # URL, unthrottled. Crawl-sourced ones go through their source's
        # throttled lanes instead — see wake_sources_with_pending_scans.
        url_ids = db.scalars(
            select(JobPostingUrl.id).where(
                JobPostingUrl.scan_status == ScanStatus.PENDING, JobPostingUrl.crawl_source_id.is_(None)
            )
        ).all()
        logger.info("Re-queuing %d pending user-submitted url(s).", len(url_ids))
        for url_id in url_ids:
            try:
                enqueue_scan(url_id)
            except Exception:
                logger.exception("Failed to re-queue url_id=%s; skipping.", url_id)

        woken = wake_sources_with_pending_scans(db)
        logger.info("Woke scan lanes for %d crawl source(s) with pending urls.", woken)
    finally:
        db.close()

    logger.info("Done.")


if __name__ == "__main__":
    main()
