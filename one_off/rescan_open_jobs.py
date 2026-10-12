"""One-off: rescan every still-open job on given domain(s), whatever its scan
status, through the normal throttled scan lanes — for picking up an adapter
fix across a whole platform.

Written for Workable (2026-10-12): its adapter had no scan_job_url, so every
Workable job was scanned by the generic HTML path, which only sees the job
page's ~250-character og:description teaser (the page is a JavaScript app).
The adapter now reads Workable's job API; this rescans the jobs stored
before that.

Only open jobs (closed_at null) are touched. Each is reset the way a human
rescan resets it and set pending, then picked up by its source's throttled
scan lanes (user-submitted ones are queued one message each) — the same
mechanics as one_off/requeue_needs_review.py. The existing posting stays
visible meanwhile: search filters on the posting, not the URL's scan status,
and a failed rescan keeps the last good posting.

Usage:
    python -m one_off.rescan_open_jobs --domain apply.workable.com --dry-run
    python -m one_off.rescan_open_jobs --domain apply.workable.com
"""

import argparse
import logging
from collections import Counter

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.models.job_url import JobPostingUrl
from app.services.job_queue import enqueue_scan, ensure_topic
from app.services.jobs import wake_sources_with_pending_scans
from one_off.requeue_needs_review import requeue

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("app.rescan_open_jobs")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", action="append", dest="domains", required=True, help="Repeatable.")
    parser.add_argument("--limit", type=int, help="At most this many jobs (newest first).")
    parser.add_argument("--dry-run", action="store_true", help="Report counts by source; change nothing.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = (
            select(JobPostingUrl.id, JobPostingUrl.crawl_source_id, CrawlSource.name)
            .outerjoin(CrawlSource, CrawlSource.id == JobPostingUrl.crawl_source_id)
            .where(JobPostingUrl.domain.in_(args.domains), JobPostingUrl.closed_at.is_(None))
            .order_by(JobPostingUrl.created_at.desc())
        )
        if args.limit:
            query = query.limit(args.limit)
        rows = db.execute(query).all()
        for name, n in Counter(name or "(no source)" for _, _, name in rows).most_common(30):
            logger.info("%6d  %s", n, name)
        logger.info("%s %d open job(s).", "Would rescan" if args.dry_run else "Rescanning", len(rows))
        if args.dry_run or not rows:
            return

        requeue(db, [url_id for url_id, _, _ in rows])
        ensure_topic()
        for url_id, source_id, _ in rows:
            if source_id is None:
                try:
                    enqueue_scan(url_id)
                except Exception:
                    logger.exception("Failed to queue url_id=%s.", url_id)
        woken = wake_sources_with_pending_scans(db)
        logger.info("Woke scan lanes for %d source(s). Done.", woken)
    finally:
        db.close()


if __name__ == "__main__":
    main()
