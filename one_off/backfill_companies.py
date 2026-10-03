"""One-off backfill: create a Company row for every company_key existing
postings have, and resolve each one's logo domain from what's already on
file — the job URL's host and its CrawlSource's board_url (the "site_host"
signal in app.services.company_logos). Nothing is refetched, so JSON-LD
domains ("jsonld") aren't available here; those fill in as postings get
rescanned (app.services.jobs._upsert_posting resolves on every scan).

A host that turns out to be shared by several companies (an ATS missing
from PLATFORM_DOMAINS) is cleared by resolve_company itself and logged as
"Shared host ...", which is worth adding to PLATFORM_DOMAINS. Manual domains
are never touched.

Idempotent: re-running only adds what's missing / upgrades by the same rules.

Usage:
    python -m one_off.backfill_companies --dry-run     # report only
    python -m one_off.backfill_companies               # write

On prod, run it from the deployed image as a one-off Cloud Run job execution
(same image/secrets/env as `migrate`, command `python -m one_off.backfill_companies`).
"""

import argparse
import logging

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.company import Company
from app.models.crawl_source import CrawlSource
from app.models.enums import ScanStatus
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services.company_logos import registrable_domain, resolve_company, usable_domain_clause

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_companies")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch-size", type=int, default=2000, help="Postings read per round trip.")
    parser.add_argument("--dry-run", action="store_true", help="Roll everything back at the end instead of committing.")
    return parser.parse_args()


def backfill(db, *, batch_size: int = 2000, commit: bool = True) -> dict:
    query = (
        select(JobPosting.id, JobPosting.company_key, JobPosting.company_name, JobPostingUrl.url, CrawlSource.board_url)
        .join(JobPostingUrl, JobPostingUrl.id == JobPosting.url_id)
        .outerjoin(CrawlSource, CrawlSource.id == JobPostingUrl.crawl_source_id)
        .where(JobPosting.company_key.is_not(None), JobPosting.extraction_status == ScanStatus.SUCCESS)
        .order_by(JobPosting.id)
    )
    # (company_key, site domain) pairs already tried — most companies have
    # many postings on the same host, and one try per pair is enough.
    tried: set[tuple[str, str | None]] = set()
    seen = 0
    last_id = None
    while True:
        batch_query = query if last_id is None else query.where(JobPosting.id > last_id)
        rows = db.execute(batch_query.limit(batch_size)).all()
        if not rows:
            break
        for row in rows:
            pair = (row.company_key, registrable_domain(row.url) or registrable_domain(row.board_url))
            if pair in tried:
                continue
            tried.add(pair)
            resolve_company(
                db,
                company_key=row.company_key,
                company_name=row.company_name,
                company_url=None,
                site_urls=[row.url, row.board_url],
            )
        seen += len(rows)
        last_id = rows[-1].id
        db.flush()
        if commit:
            db.commit()
        logger.info("%d postings processed", seen)

    total = db.scalar(select(func.count()).select_from(Company))
    with_domain = db.scalar(select(func.count()).select_from(Company).where(usable_domain_clause()))
    return {"postings": seen, "companies": total, "with_domain": with_domain}


def main() -> None:
    args = _parse_args()
    db = SessionLocal()
    try:
        stats = backfill(db, batch_size=args.batch_size, commit=not args.dry_run)
        logger.info(
            "Done%s. %d postings read; %d companies, %d with a logo domain (%.0f%%).",
            " (dry-run, rolled back)" if args.dry_run else "",
            stats["postings"],
            stats["companies"],
            stats["with_domain"],
            100 * stats["with_domain"] / max(stats["companies"], 1),
        )
        if args.dry_run:
            db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    main()
