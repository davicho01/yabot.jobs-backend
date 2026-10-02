"""Hourly fan-out for the discovery crawl.

Publishes one Pub/Sub message per *due* CrawlSource to the
crawl-source-requests topic, then exits — crawl_worker.py does the actual
per-source discovery work, so many boards are crawled in parallel by
however many crawl-worker instances are running.

Each source is crawled about every settings.crawl_interval_hours (12h), but
not all at once: a run dispatches at most active ÷ crawl_interval_hours
sources (one hourly run's share), choosing the due ones that have waited
longest. Dispatching all ~3,000 sources in two daily bursts used to start
every crawl and scan together and run Cloud SQL out of connection slots
(1,838 refused connections in one burst on 2026-10-01).

Nothing assigns a source to an hour. A source comes due again
crawl_interval_hours after its own last crawl, and each run only releases
its budget of them, so a crowd that all came due at once (the first run
after this shipped, or an outage) gets dealt out over successive hours. Each
source's crawl time then carries forward to the same hour of the next
cycle. A brand-new source has no last_crawled_at, so it goes first.

"Unclaimed" (CrawlSource.crawl_claimed_at is null or older than
settings.crawl_claim_ttl_seconds) still matters: a source whose previous
crawl message hasn't been processed yet must not get another one piled on
top. Verified live before claims existed: a 235,000-message backlog against
~2,800 active sources (see that column's own comment on the model).

Meant to run hourly (deploy/gcloud-deploy.sh's crawl-dispatch-hourly) — the
budget assumes it. Run more often and sources still won't be crawled before
they're due, just in smaller groups.

Usage: python crawl_dispatcher.py
"""

import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from app.core.config import settings
from app.core.log_config import configure_logging
from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.models.enums import CrawlSourceStatus
from app.services.crawl_queue import enqueue_crawl, ensure_topic
from app.services.gcp_admin import warm_up_browser_scaler
from app.services.jobs import wake_sources_with_pending_scans

configure_logging()
logger = logging.getLogger("app.crawl_dispatcher")


def main() -> None:
    # yabot-jobs-browser (the shared headless-Chromium rendering service
    # several ATS adapters need — see app.services.browser_fetch) sits at
    # min-instances=0 between dispatch cycles to avoid paying for warm
    # Chromium instances 24/7 — see gcp_admin.warm_up_browser_scaler's own
    # docstring for why this has to happen here (and in
    # retry_failed_scans.py) rather than relying on browser_scaler.py alone.
    warm_up_browser_scaler()

    ensure_topic()

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=settings.crawl_claim_ttl_seconds)
    # Half an hour short of the interval, so a source crawled a few minutes
    # into its hour is due again at the top of that hour, not an hour late.
    due_before = now - timedelta(hours=settings.crawl_interval_hours - 0.5)

    db = SessionLocal()
    try:
        active = db.scalar(
            select(func.count()).select_from(CrawlSource).where(CrawlSource.status == CrawlSourceStatus.ACTIVE)
        ) or 0
        budget = math.ceil(active / settings.crawl_interval_hours)
        due = (
            CrawlSource.status == CrawlSourceStatus.ACTIVE,
            or_(CrawlSource.crawl_claimed_at.is_(None), CrawlSource.crawl_claimed_at <= cutoff),
            or_(CrawlSource.last_crawled_at.is_(None), CrawlSource.last_crawled_at <= due_before),
        )
        due_count = db.scalar(select(func.count()).select_from(CrawlSource).where(*due)) or 0
        sources = db.scalars(
            select(CrawlSource)
            .where(*due)
            # Never-crawled first, then whoever has waited longest.
            .order_by(CrawlSource.last_crawled_at.is_not(None), CrawlSource.last_crawled_at, CrawlSource.id)
            .limit(budget)
        ).all()
        logger.info(
            "%d of %d active source(s) due; dispatching %d (budget %d/run), %d left for later runs.",
            due_count, active, len(sources), budget, due_count - len(sources),
        )
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


if __name__ == "__main__":
    main()
