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
import uuid

from sqlalchemy import func, insert, select

from app.db.session import SessionLocal
from app.models.company import Company
from app.models.crawl_source import CrawlSource
from app.models.enums import ScanStatus
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services.company_logos import is_platform_domain, registrable_domain, resolve_company, usable_domain_clause

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_companies")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch-size", type=int, default=1000, help="Company rows inserted per statement.")
    parser.add_argument("--dry-run", action="store_true", help="Roll everything back at the end instead of committing.")
    return parser.parse_args()


def backfill(db, *, batch_size: int = 1000, commit: bool = True) -> dict:
    """Two set-based passes, so it fits prod's size (hundreds of thousands
    of postings) in minutes rather than doing several database round trips
    per posting:

    1. Create a Company row for every company_key that lacks one — all a
       company needs for the logo sync, whose logo.dev name search finds
       most domains on its own.
    2. Resolve domains per distinct (company, job host, crawl source board)
       combination — and only where one of those hosts isn't a known
       platform, i.e. a company's own careers site (careers.walmart.com).
    """
    succeeded = (JobPosting.company_key.is_not(None), JobPosting.extraction_status == ScanStatus.SUCCESS)

    names = db.execute(select(JobPosting.company_key, func.max(JobPosting.company_name)).where(*succeeded).group_by(JobPosting.company_key)).all()
    existing = set(db.scalars(select(Company.company_key)))
    new_rows = [
        {"id": uuid.uuid4(), "company_key": key, "display_name": (name or key)[:255]}
        for key, name in names
        if key not in existing
    ]
    for start in range(0, len(new_rows), batch_size):
        db.execute(insert(Company), new_rows[start : start + batch_size])
        if commit:
            db.commit()
    logger.info("%d companies created (%d already existed)", len(new_rows), len(existing))

    combos = db.execute(
        select(JobPosting.company_key, func.max(JobPosting.company_name), JobPostingUrl.domain, CrawlSource.board_url)
        .join(JobPostingUrl, JobPostingUrl.id == JobPosting.url_id)
        .outerjoin(CrawlSource, CrawlSource.id == JobPostingUrl.crawl_source_id)
        .where(*succeeded)
        .group_by(JobPosting.company_key, JobPostingUrl.domain, CrawlSource.board_url)
    ).all()
    resolved = 0
    tried: set[tuple[str, str | None]] = set()
    for key, name, host, board_url in combos:
        site_urls = [f"https://{host}/" if host else None, board_url]
        own = [d for d in (registrable_domain(u) for u in site_urls) if d and not is_platform_domain(d)]
        if not own or (key, own[0]) in tried:
            continue  # only ATS/job-board hosts: nothing to learn here
        tried.add((key, own[0]))
        resolve_company(db, company_key=key, company_name=name, company_url=None, site_urls=site_urls)
        resolved += 1
        if commit and resolved % 500 == 0:
            db.commit()
            logger.info("%d company/site combinations resolved", resolved)
    if commit:
        db.commit()

    total = db.scalar(select(func.count()).select_from(Company))
    with_domain = db.scalar(select(func.count()).select_from(Company).where(usable_domain_clause()))
    return {"created": len(new_rows), "combinations": resolved, "companies": total, "with_domain": with_domain}


def main() -> None:
    args = _parse_args()
    db = SessionLocal()
    try:
        stats = backfill(db, batch_size=args.batch_size, commit=not args.dry_run)
        logger.info(
            "Done%s. %d companies created, %d company/site combinations resolved; %d companies, %d with a domain (%.0f%%).",
            " (dry-run, rolled back)" if args.dry_run else "",
            stats["created"],
            stats["combinations"],
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
