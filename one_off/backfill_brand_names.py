"""One-off backfill: replace legal-entity company names that carry a tax ID
or company code ("2100 NVIDIA USA", "94-1687665 Bank of America, National
Association") with the brand their crawl source is named after ("Nvidia",
"Bank of America") — what app.services.jobs._upsert_posting now does on
every scan (see job_dedup.has_entity_code), applied to existing postings
without waiting for a rescan. company_key is recomputed to match, so these
postings join the brand's Company (and its logo) and dedup against it.

Only postings from a crawl source named like a brand (not a "lever/slug"
board name) are touched. Idempotent.

Usage:
    python -m one_off.backfill_brand_names --dry-run
    python -m one_off.backfill_brand_names

On prod, run it from the deployed image as a one-off Cloud Run job execution
(same image/secrets/env as `migrate`, command `python -m one_off.backfill_brand_names`).
"""

import argparse
import logging

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services.company_logos import resolve_company
from app.services.job_dedup import has_entity_code, normalize_company_name

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_brand_names")


def backfill(db, *, dry_run: bool = False) -> dict[str, int]:
    rows = db.execute(
        select(JobPosting, CrawlSource.name, JobPostingUrl.url, CrawlSource.board_url)
        .join(JobPostingUrl, JobPostingUrl.id == JobPosting.url_id)
        .join(CrawlSource, CrawlSource.id == JobPostingUrl.crawl_source_id)
        .where(JobPosting.company_name.op("~")(r"^([0-9]{2}-[0-9]{7}|[0-9]{3,})\s"))
    ).all()
    renamed: dict[str, int] = {}
    for posting, source_name, url, board_url in rows:
        if not has_entity_code(posting.company_name) or "/" in source_name:
            continue
        label = f"{posting.company_name} -> {source_name}"
        renamed[label] = renamed.get(label, 0) + 1
        posting.company_name = source_name
        posting.company_key = normalize_company_name(source_name)
        # Make sure the brand has its Company row (domain resolved from the
        # same signals a scan uses); its logo comes with the next sync.
        resolve_company(
            db,
            company_key=posting.company_key,
            company_name=source_name,
            company_url=None,
            site_urls=[url, board_url],
        )
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return renamed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing anything.")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        renamed = backfill(db, dry_run=args.dry_run)
        for label, count in sorted(renamed.items()):
            logger.info("%s (%d posting%s)", label, count, "" if count == 1 else "s")
        logger.info("Done%s. %d postings renamed.", " (dry run)" if args.dry_run else "", sum(renamed.values()))
    finally:
        db.close()


if __name__ == "__main__":
    main()
