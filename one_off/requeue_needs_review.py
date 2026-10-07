"""One-off recovery: give still-open jobs stuck in needs_review a fresh
retry budget and queue them for a rescan.

Between 2026-09-26 and 2026-10-05 the browser-render service was down or
overloaded, and every job page that needs it (bot walls answering 403/202,
which is most of Avature, Talemetry, Clinch and many Greenhouse-backed
career sites) failed all its automatic retries and was left in
needs_review — a dead end until a human rescans it. A sample of 300 on
2026-10-06: 61% bot walls (rendering works again), 26% plain-fetchable now,
6% removed (404 — now closed as expired, see base.PostingGone), 6% transient.

Only open jobs (closed_at null: their board still lists them) are touched.
Each is reset the way a human rescan resets it (scan_attempts 0) and set
pending; crawl-sourced ones are then picked up by their source's throttled
scan lanes, user-submitted ones are queued one message each — the same
wake-up one_off/requeue_pending_scans.py does.

Usage:
    python -m one_off.requeue_needs_review --dry-run          # counts by source
    python -m one_off.requeue_needs_review                    # requeue them all
    python -m one_off.requeue_needs_review --ats avature --limit 500
"""

import argparse
import logging
from collections import Counter

from sqlalchemy import select, update

from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.models.enums import ScanStatus
from app.models.job_url import JobPostingUrl
from app.services.job_queue import enqueue_scan, ensure_topic
from app.services.jobs import wake_sources_with_pending_scans

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("app.requeue_needs_review")

BATCH_SIZE = 500


def stuck_urls(db, *, ats: str | None, limit: int | None) -> list[tuple]:
    """[(url_id, crawl_source_id, source name)] of open needs_review jobs."""
    query = (
        select(JobPostingUrl.id, JobPostingUrl.crawl_source_id, CrawlSource.name)
        .outerjoin(CrawlSource, CrawlSource.id == JobPostingUrl.crawl_source_id)
        .where(JobPostingUrl.scan_status == ScanStatus.NEEDS_REVIEW, JobPostingUrl.closed_at.is_(None))
        .order_by(JobPostingUrl.created_at.desc())
    )
    if ats:
        query = query.where(CrawlSource.ats_type == ats)
    if limit:
        query = query.limit(limit)
    return db.execute(query).all()


def requeue(db, url_ids: list) -> None:
    for start in range(0, len(url_ids), BATCH_SIZE):
        db.execute(
            update(JobPostingUrl)
            .where(JobPostingUrl.id.in_(url_ids[start : start + BATCH_SIZE]))
            .values(scan_status=ScanStatus.PENDING, scan_attempts=0, next_retry_at=None, scan_claimed_at=None)
            .execution_options(synchronize_session=False)
        )
        db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ats", help="Only jobs from sources on this platform (ats_type).")
    parser.add_argument("--limit", type=int, help="At most this many jobs (newest first).")
    parser.add_argument("--dry-run", action="store_true", help="Report counts by source; change nothing.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rows = stuck_urls(db, ats=args.ats, limit=args.limit)
        for name, n in Counter(name or "(no source)" for _, _, name in rows).most_common(30):
            logger.info("%6d  %s", n, name)
        logger.info("%s %d open needs_review job(s).", "Would requeue" if args.dry_run else "Requeuing", len(rows))
        if args.dry_run or not rows:
            return

        requeue(db, [url_id for url_id, _, _ in rows])
        ensure_topic()
        for url_id, source_id, _ in rows:
            if source_id is None:
                try:
                    enqueue_scan(url_id)
                except Exception:
                    logger.exception("Failed to queue url_id=%s; the hourly retry sweep won't pick it up.", url_id)
        woken = wake_sources_with_pending_scans(db)
        logger.info("Woke scan lanes for %d source(s). Done.", woken)
    finally:
        db.close()


if __name__ == "__main__":
    main()
