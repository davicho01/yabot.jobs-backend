"""One-shot fan-out for the daily discovery crawl.

Reads every active, currently-unclaimed CrawlSource and publishes one Pub/Sub
message per source to the crawl-source-requests topic, then exits —
crawl_worker.py does the actual per-source discovery work. Keeping dispatch
and processing separate means many companies/boards can be crawled in
parallel by running multiple crawl_worker.py processes, instead of one
script looping through every source sequentially.

"Currently-unclaimed" (CrawlSource.crawl_claimed_at is null or older than
settings.crawl_claim_ttl_seconds) matters because this is meant to run
frequently (every couple hours) so new postings surface quickly, but each
crawl-worker invocation can take a while (fetching a whole board) — without
this check, a source whose previous wake-up hadn't been processed yet just
got *another* message piled on top every single cycle, run after run,
forever, regardless of whether crawl-worker was keeping up. Verified live:
this had built up a 235,000-message backlog against ~2,800 active sources
(crawl_claimed_at was added specifically to fix this — see that column's own
comment on the model).

This script doesn't know or care how it's invoked — point any scheduler at
it (cron, GCP Cloud Scheduler + a Cloud Run Job, GitHub Actions, etc.) with
whatever cadence you want.

Usage: python crawl_dispatcher.py
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from app.core.config import settings
from app.core.log_config import configure_logging
from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.models.enums import CrawlSourceStatus
from app.services.crawl_queue import enqueue_crawl, ensure_topic_and_subscription
from app.services.jobs import wake_sources_with_pending_scans

configure_logging()
logger = logging.getLogger("app.crawl_dispatcher")


def main() -> None:
    ensure_topic_and_subscription()

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=settings.crawl_claim_ttl_seconds)

    db = SessionLocal()
    try:
        sources = db.scalars(
            select(CrawlSource).where(
                CrawlSource.status == CrawlSourceStatus.ACTIVE,
                or_(CrawlSource.crawl_claimed_at.is_(None), CrawlSource.crawl_claimed_at <= cutoff),
            )
        ).all()
        logger.info("Dispatching crawl for %d unclaimed active source(s).", len(sources))
        for source in sources:
            # Claimed (and committed) *before* publishing, not after: if
            # publishing then fails, the source just waits out the TTL
            # before being retried — worse than duplicating a message, since
            # a missed commit after a successful publish would leave a
            # message in flight with no claim recorded, letting the next
            # cycle pile another one on top of it (exactly the bug this
            # column exists to prevent).
            source.crawl_claimed_at = now
            db.commit()
            try:
                enqueue_crawl(source.id)
                logger.info("Enqueued crawl for %s (%s: %s)", source.name, source.ats_type, source.board_url)
            except Exception:
                logger.exception("Failed to enqueue crawl for %s; will retry after the claim TTL.", source.name)

        # Safety net for per-source scan throttling: a crawl wakes its own
        # scan lanes, but a lost wake-up or a lane that died mid-drain would
        # otherwise strand PENDING URLs until that source next discovers
        # something new. Also drains anything left over from before
        # throttling shipped.
        try:
            woken = wake_sources_with_pending_scans(db)
            logger.info("Woke scan lanes for %d source(s) with pending URLs.", woken)
        except Exception:
            logger.exception("Sweep for pending scans failed.")
    finally:
        db.close()


def dispatch(_request=None) -> tuple[str, int]:
    """Cloud Functions (2nd gen) HTTP entry point — Cloud Scheduler calls this
    on a cron cadence instead of a shell invoking main() directly."""
    main()
    return "ok", 200


if __name__ == "__main__":
    main()
