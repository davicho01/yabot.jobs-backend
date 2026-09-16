"""One-shot fan-out for the daily discovery crawl.

Reads every active CrawlSource and publishes one Pub/Sub message per source
to the crawl-source-requests topic, then exits — crawl_worker.py does the
actual per-source discovery work. Keeping dispatch and processing separate
means many companies/boards can be crawled in parallel by running multiple
crawl_worker.py processes, instead of one script looping through every
source sequentially.

This script doesn't know or care how it's invoked — point any scheduler at
it (cron, GCP Cloud Scheduler + a Cloud Run Job, GitHub Actions, etc.) with
whatever cadence you want (e.g. daily).

Usage: python crawl_dispatcher.py
"""

import logging

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.models.enums import CrawlSourceStatus
from app.services.crawl_queue import enqueue_crawl, ensure_topic_and_subscription

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("app.crawl_dispatcher")


def main() -> None:
    ensure_topic_and_subscription()

    db = SessionLocal()
    try:
        sources = db.scalars(select(CrawlSource).where(CrawlSource.status == CrawlSourceStatus.ACTIVE)).all()
        logger.info("Dispatching crawl for %d active source(s).", len(sources))
        for source in sources:
            try:
                enqueue_crawl(source.id)
                logger.info("Enqueued crawl for %s (%s: %s)", source.name, source.ats_type, source.board_url)
            except Exception:
                logger.exception("Failed to enqueue crawl for %s; skipping.", source.name)
    finally:
        db.close()


def dispatch(_request=None) -> tuple[str, int]:
    """Cloud Functions (2nd gen) HTTP entry point — Cloud Scheduler calls this
    on a cron cadence instead of a shell invoking main() directly."""
    main()
    return "ok", 200


if __name__ == "__main__":
    main()
