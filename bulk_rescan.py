"""One-off backfill: rescan existing JobPostingUrl rows so already-stored
postings pick up extractor improvements (e.g. Greenhouse/Apple/Amazon/Google
company name, location, posted_at) without waiting for a natural rescan.

Defaults to every URL whose JobPosting is missing company_name, location, or
posted_at — the set of rows an extractor fix could actually change — rather
than a hardcoded domain list, so it stays correct as extractors improve
without needing to be kept in sync by hand. Pass --domain to narrow to
specific domain(s) instead (e.g. while testing a single-site fix).

Usage:
    python bulk_rescan.py                                  # all fixable rows
    python bulk_rescan.py --domain job-boards.greenhouse.io # one domain
    python bulk_rescan.py --limit 20 --dry-run              # preview only
"""

import argparse
import logging
import time

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services.jobs import rescan_job_url

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("app.bulk_rescan")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--domain",
        action="append",
        dest="domains",
        help="Only rescan this domain (repeatable). Default: any domain with a fixable gap.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Rescan at most this many URLs.")
    parser.add_argument(
        "--delay", type=float, default=0.5, help="Seconds to sleep between requests (default: 0.5)."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="List matching URLs without actually rescanning them."
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    db = SessionLocal()
    try:
        query = select(JobPostingUrl).join(JobPosting, JobPosting.url_id == JobPostingUrl.id)
        if args.domains:
            query = query.where(JobPostingUrl.domain.in_(args.domains))
        else:
            query = query.where(
                JobPosting.company_name.is_(None)
                | JobPosting.location.is_(None)
                | JobPosting.posted_at.is_(None)
            )
        query = query.order_by(JobPostingUrl.domain)
        if args.limit:
            query = query.limit(args.limit)

        url_rows = db.scalars(query).all()
        logger.info("Found %d URL(s) to rescan.", len(url_rows))

        if args.dry_run:
            for url_row in url_rows:
                logger.info("[dry-run] would rescan %s (%s)", url_row.url, url_row.domain)
            return

        succeeded = failed = 0
        for i, url_row in enumerate(url_rows, start=1):
            try:
                rescan_job_url(db, url_row)
                db.commit()
            except Exception:
                db.rollback()
                failed += 1
                logger.exception("(%d/%d) failed to rescan %s; skipping.", i, len(url_rows), url_row.url)
                continue

            succeeded += 1
            posting = db.scalar(select(JobPosting).where(JobPosting.url_id == url_row.id))
            logger.info(
                "(%d/%d) rescanned %s -> company=%r location=%r posted_at=%s",
                i,
                len(url_rows),
                url_row.url,
                posting.company_name if posting else None,
                posting.location if posting else None,
                posting.posted_at if posting else None,
            )

            if args.delay:
                time.sleep(args.delay)

        logger.info("Done. %d succeeded, %d failed.", succeeded, failed)
    finally:
        db.close()


if __name__ == "__main__":
    main()
