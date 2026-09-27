"""One-off backfill: fill in JobPosting.company_name for postings where the
scrape came back with no usable name and the URL was discovered by a
CrawlSource — verified live that Capital One's Workday tenant serves
hiringOrganization.name as "" on every job, and app.services.jobs._upsert_posting
falls back to the discovering CrawlSource's name (see that fix) only going
forward, on the next scan/rescan. This brings existing NULL rows in line
without waiting for a rescan.

Reads only postings with company_name IS NULL joined to a JobPostingUrl that
has a crawl_source_id, and writes CrawlSource.name for each. User-submitted
postings with no crawl_source_id are left alone (nothing sensible to fall
back to). Idempotent: re-running only touches rows still NULL.

Usage:
    python -m one_off.backfill_company_name --dry-run     # report only
    python -m one_off.backfill_company_name               # write

On prod, run it from the deployed image as a one-off Cloud Run job execution
(same image/secrets/env as `migrate`, command `python -m one_off.backfill_company_name`).
"""

import argparse
import logging

from sqlalchemy import bindparam, select, update

from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_company_name")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch-size", type=int, default=1000, help="Postings read/written per round trip.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing anything.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    table = JobPosting.__table__
    write = update(table).where(table.c.id == bindparam("posting_id")).values(company_name=bindparam("new_name"))

    query = (
        select(JobPosting.id, CrawlSource.name)
        .join(JobPostingUrl, JobPostingUrl.id == JobPosting.url_id)
        .join(CrawlSource, CrawlSource.id == JobPostingUrl.crawl_source_id)
        .where(JobPosting.company_name.is_(None))
        .order_by(JobPosting.id)
    )

    db = SessionLocal()
    try:
        seen = 0
        last_id = None
        while True:
            batch_query = query if last_id is None else query.where(JobPosting.id > last_id)
            rows = db.execute(batch_query.limit(args.batch_size)).all()
            if not rows:
                break

            updates = [{"posting_id": row.id, "new_name": row.name} for row in rows]
            seen += len(rows)
            last_id = rows[-1].id

            if not args.dry_run:
                db.execute(write, updates)
                db.commit()
            logger.info("%d postings %s so far", seen, "would be filled in (dry-run)" if args.dry_run else "filled in")

        verb = "would fill in (dry-run, nothing written)" if args.dry_run else "filled in"
        logger.info("Done. company_name %s on %d postings.", verb, seen)
    finally:
        db.close()


if __name__ == "__main__":
    main()
