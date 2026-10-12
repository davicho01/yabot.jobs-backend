"""One-off: mark already-stored incomplete jobs failed, so they're hidden from
search and retried.

New scans are checked as they happen (app.services.scan_completeness), but
jobs stored before that check still count as successful with a teaser or
empty description — ~4.4% of open jobs on 2026-10-12 (Workable, Greenhouse,
Gupy, Best Buy, JazzHR, ...). This gives each open one whose description is
under settings.scan_min_description_chars the state a new incomplete scan
gets in app.services.jobs._apply_scan_result:

  - URL failed, scan_error "Incomplete scan: ...", scan_attempts 1 (the
    stored scan counts as the first attempt), next_retry_at spread at random
    over the next --spread-hours so the hourly retry sweep
    (wake_retryable_failed_scans) picks them up gradually instead of all at
    once;
  - posting extraction_status failed, which takes it out of search, similar
    jobs, saved-search alerts and the static pages until a retry completes it.

Only URLs currently at success are touched (never one being scanned right
now). Walks job_posting_urls by id in batches; descriptions are pre-filtered
by byte length in SQL (bytes >= characters, so 4x covers any UTF-8) and then
checked exactly with the same helper new scans use.

Usage:
    python -m one_off.mark_incomplete_scans --dry-run
    python -m one_off.mark_incomplete_scans --limit 50
    python -m one_off.mark_incomplete_scans --domain apply.workable.com
    python -m one_off.mark_incomplete_scans                 # everything, spread over 24h
"""

import argparse
import logging
import random
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.models.enums import ScanStatus
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services.scan_completeness import INCOMPLETE_SCAN_ERROR_PREFIX, description_reasons

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("app.mark_incomplete_scans")

BATCH_SIZE = 2000


def _candidates(db, after_id, domains: list[str] | None):
    """Up to BATCH_SIZE (url_id, posting_id, description, domain, ats_type)
    rows past after_id that might be incomplete — exact check is the caller's."""
    stmt = (
        select(JobPostingUrl.id, JobPosting.id, JobPosting.description, JobPostingUrl.domain, CrawlSource.ats_type)
        .join(JobPosting, JobPosting.url_id == JobPostingUrl.id)
        .outerjoin(CrawlSource, CrawlSource.id == JobPostingUrl.crawl_source_id)
        .where(
            JobPostingUrl.closed_at.is_(None),
            JobPostingUrl.scan_status == ScanStatus.SUCCESS,
            JobPosting.extraction_status == ScanStatus.SUCCESS,
            func.coalesce(func.octet_length(JobPosting.description), 0) < 4 * settings.scan_min_description_chars,
        )
        .order_by(JobPostingUrl.id)
        .limit(BATCH_SIZE)
    )
    if after_id is not None:
        stmt = stmt.where(JobPostingUrl.id > after_id)
    if domains:
        stmt = stmt.where(JobPostingUrl.domain.in_(domains))
    return db.execute(stmt).all()


def _mark(db, rows, now: datetime, spread_hours: float) -> None:
    db.execute(
        update(JobPostingUrl),
        [
            {
                "id": url_id,
                "scan_status": ScanStatus.FAILED,
                "scan_error": f"{INCOMPLETE_SCAN_ERROR_PREFIX}{'; '.join(reasons)}",
                "scan_attempts": 1,
                "scan_claimed_at": None,
                "next_retry_at": now + timedelta(seconds=random.uniform(0, spread_hours * 3600)),
            }
            for url_id, _, reasons in rows
        ],
    )
    db.execute(
        update(JobPosting)
        .where(JobPosting.id.in_([posting_id for _, posting_id, _ in rows]))
        .values(extraction_status=ScanStatus.FAILED)
        .execution_options(synchronize_session=False)
    )
    db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", action="append", dest="domains", help="Only this domain (repeatable).")
    parser.add_argument("--limit", type=int, help="Mark at most this many jobs.")
    parser.add_argument("--spread-hours", type=float, default=24.0, help="Spread retries over this many hours.")
    parser.add_argument("--dry-run", action="store_true", help="Report counts; change nothing.")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    by_platform: Counter = Counter()
    by_domain: Counter = Counter()
    marked = 0
    after_id = None
    db = SessionLocal()
    try:
        while args.limit is None or marked < args.limit:
            candidates = _candidates(db, after_id, args.domains)
            if not candidates:
                break
            after_id = candidates[-1][0]
            rows = []
            for url_id, posting_id, description, domain, ats_type in candidates:
                reasons = description_reasons(description)
                if not reasons:
                    continue
                if args.limit is not None and marked + len(rows) >= args.limit:
                    break
                rows.append((url_id, posting_id, reasons))
                by_platform[ats_type or "(no source)"] += 1
                by_domain[domain] += 1
            if rows and not args.dry_run:
                _mark(db, rows, now, args.spread_hours)
            marked += len(rows)
            if marked and marked % 5000 < len(rows):
                logger.info("... %d so far", marked)

        for name, n in by_platform.most_common(25):
            logger.info("%7d  %s", n, name)
        logger.info("Top domains: %s", ", ".join(f"{d} ({n})" for d, n in by_domain.most_common(10)))
        logger.info(
            "%s %d incomplete job(s)%s.",
            "Would mark" if args.dry_run else "Marked",
            marked,
            "" if args.dry_run else f" failed, retries spread over {args.spread_hours:g}h",
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
