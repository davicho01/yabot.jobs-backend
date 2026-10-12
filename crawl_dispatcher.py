"""One-shot fan-out for the discovery crawl.

Reads the active, currently-unclaimed CrawlSources that are due (each source
has its own adaptive crawl interval — see app.services.crawl_schedule) and
publishes one Pub/Sub message per source to the crawl-source-requests topic,
at most settings.crawl_dispatch_batch_size per run, then exits —
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

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

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


def _due_sources(db: Session, now: datetime) -> list[CrawlSource]:
    """Active official sources that are unclaimed and due (see
    app.services.crawl_schedule), most overdue first, at most
    settings.crawl_dispatch_batch_size of them — the rest wait for the next
    run, so a backlog (Monday morning, a first rollout) is spread out instead
    of bursting crawl-worker and Cloud SQL.

    Due means next_crawl_at has passed (NULL: never scheduled), or the last
    crawl is older than the current maximum interval — so lowering
    settings.crawl_max_interval_minutes takes effect right away instead of
    waiting out next_crawl_at values set under the old maximum.
    """
    claim_cutoff = now - timedelta(seconds=settings.crawl_claim_ttl_seconds)
    max_interval_cutoff = now - timedelta(minutes=settings.crawl_max_interval_minutes)
    # Only companies' own sites are crawled: a job board (is_official
    # false) re-lists other companies' jobs, which their official sites
    # already give us first-hand (see app.services.company_names).
    return list(
        db.scalars(
            select(CrawlSource)
            .where(
                CrawlSource.status == CrawlSourceStatus.ACTIVE,
                CrawlSource.is_official.is_(True),
                or_(CrawlSource.crawl_claimed_at.is_(None), CrawlSource.crawl_claimed_at <= claim_cutoff),
                or_(
                    CrawlSource.next_crawl_at.is_(None),
                    CrawlSource.next_crawl_at <= now,
                    CrawlSource.last_crawled_at <= max_interval_cutoff,
                ),
            )
            .order_by(CrawlSource.next_crawl_at.asc().nulls_first())
            .limit(settings.crawl_dispatch_batch_size)
        )
    )


def _log_interval_distribution(db: Session) -> None:
    """One line for tuning the schedule: how many active sources sit at each
    crawl interval."""
    interval = CrawlSource.crawl_interval_minutes
    tier = case(
        (interval <= 120, "<=2h"),
        (interval <= 720, "<=12h"),
        (interval <= 1440, "<=1d"),
        else_=">1d",
    )
    counts = dict(
        db.execute(
            select(tier, func.count())
            .where(CrawlSource.status == CrawlSourceStatus.ACTIVE, CrawlSource.is_official.is_(True))
            .group_by(tier)
        ).all()
    )
    logger.info("Crawl interval distribution: %s", counts)


def main() -> None:
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        sources = _due_sources(db, now)
        logger.info("Dispatching crawl for %d due source(s).", len(sources))
        if sources:
            # yabot-jobs-browser (the shared headless-Chromium rendering
            # service several ATS adapters and many job pages need — see
            # app.services.browser_fetch) sits at min-instances=0 between
            # dispatch cycles to avoid paying for warm Chromium instances
            # 24/7 — see gcp_admin.warm_up_browser_scaler's own docstring for
            # why this has to happen here (and in retry_failed_scans.py)
            # rather than relying on browser_scaler.py alone. Skipped when
            # nothing is due: no crawls, no scans to warm up for.
            warm_up_browser_scaler()
            ensure_topic()
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

        _log_interval_distribution(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
