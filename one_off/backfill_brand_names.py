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

from sqlalchemy import select, update

from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services.company_logos import resolve_company
from app.services.job_dedup import has_entity_code, normalize_company_name

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_brand_names")


BATCH_SIZE = 1000


def backfill(db, *, dry_run: bool = False) -> dict[str, int]:
    # Just the columns needed — whole JobPosting rows carry each job's full
    # description and scanned HTML, which ran the prod job out of memory.
    rows = db.execute(
        select(JobPosting.id, JobPosting.company_name, CrawlSource.name, JobPostingUrl.url, CrawlSource.board_url)
        .join(JobPostingUrl, JobPostingUrl.id == JobPosting.url_id)
        .join(CrawlSource, CrawlSource.id == JobPostingUrl.crawl_source_id)
        .where(JobPosting.company_name.op("~")(r"^([0-9]{2}-[0-9]{7}|[0-9]{3,})\s"))
    ).all()
    # (old name, brand) -> posting ids, plus one URL pair per brand for
    # resolving its Company row.
    groups: dict[tuple[str, str], list] = {}
    brand_urls: dict[str, tuple[str, str]] = {}
    for posting_id, company_name, source_name, url, board_url in rows:
        if not has_entity_code(company_name) or "/" in source_name:
            continue
        groups.setdefault((company_name, source_name), []).append(posting_id)
        brand_urls.setdefault(source_name, (url, board_url))

    renamed = {f"{old} -> {brand}": len(ids) for (old, brand), ids in groups.items()}
    if dry_run:
        return renamed
    for (old, brand), ids in groups.items():
        key = normalize_company_name(brand)
        for start in range(0, len(ids), BATCH_SIZE):
            db.execute(
                update(JobPosting)
                .where(JobPosting.id.in_(ids[start : start + BATCH_SIZE]))
                .values(company_name=brand, company_key=key)
            )
        db.commit()
    for brand, (url, board_url) in brand_urls.items():
        # Make sure the brand has its Company row (domain resolved from the
        # same signals a scan uses); its logo comes with the next sync.
        resolve_company(
            db, company_key=normalize_company_name(brand), company_name=brand, company_url=None, site_urls=[url, board_url]
        )
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
