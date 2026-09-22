"""One-off backfill: compute company_key/title_key and primary_posting_id
(see app.services.job_dedup) for every existing, successfully-scanned
JobPosting — these are normally computed live by _upsert_posting on each
scan/rescan, so this only needs to run once, right after the migration that
adds the columns, to cover rows that predate cross-source dedup.

Processes oldest JobPostingUrl first and flushes after each row, so a later
posting's dedup match sees every earlier row's already-computed keys and
(if any) its own primary_posting_id — the same "oldest wins as canonical,
duplicates chain to it not to each other" invariant find_duplicate_primary
relies on live. Purely a DB computation (no network fetches), so unlike
bulk_rescan.py this doesn't need a --delay between rows.

Usage:
    python backfill_dedup_keys.py                # all successfully-scanned postings
    python backfill_dedup_keys.py --limit 20 --dry-run
"""

import argparse
import logging

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.enums import ScanStatus
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services.job_dedup import find_duplicate_primary, normalize_company_name, normalize_title

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_dedup_keys")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="Process at most this many postings.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Log what would change without writing it."
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    db = SessionLocal()
    try:
        query = (
            select(JobPosting)
            .join(JobPostingUrl, JobPosting.url_id == JobPostingUrl.id)
            .where(JobPosting.extraction_status == ScanStatus.SUCCESS)
            .order_by(JobPostingUrl.created_at)
        )
        if args.limit:
            query = query.limit(args.limit)

        postings = db.scalars(query).all()
        logger.info("Found %d successfully-scanned posting(s) to key.", len(postings))

        grouped = 0
        for i, posting in enumerate(postings, start=1):
            company_key = normalize_company_name(posting.company_name)
            title_key = normalize_title(posting.title)

            if args.dry_run:
                logger.info(
                    "[dry-run] (%d/%d) %r at %r -> company_key=%r title_key=%r",
                    i, len(postings), posting.title, posting.company_name, company_key, title_key,
                )
                continue

            posting.company_key = company_key
            posting.title_key = title_key
            db.flush()  # so this row's keys are visible to later rows' dedup queries below

            primary_id = find_duplicate_primary(db, posting)
            posting.primary_posting_id = primary_id
            db.commit()

            if primary_id is not None:
                grouped += 1
                logger.info("(%d/%d) %r at %r -> duplicate of %s", i, len(postings), posting.title, posting.company_name, primary_id)

        if not args.dry_run:
            logger.info("Done. %d posting(s) grouped as duplicates of an earlier one.", grouped)
    finally:
        db.close()


if __name__ == "__main__":
    main()
