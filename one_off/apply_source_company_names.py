"""One-off: rename every job to its crawl source's official company name (or
a configured sub-brand) — app.services.company_names.company_for, which every
scan now applies, applied to existing jobs without waiting for a rescan.
Jobs with no crawl source get the cleaned page name. Run after
one_off/curate_source_names.py has made the source names usable.

The dry run lists, per source, the most common old -> new name changes with
counts — review all of it before writing. Writing recomputes company_name and
company_key and makes sure each resulting company has its Company row (domain,
logo); the static pages follow on the next page runs (or run the backfill
job with --full). Idempotent.

Usage:
    python -m one_off.apply_source_company_names --dry-run
    python -m one_off.apply_source_company_names
"""

import argparse
import logging
from collections import Counter, defaultdict

from sqlalchemy import select, update

from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services.company_names import apply_source_company, company_for
from app.services.job_dedup import normalize_company_name

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("app.apply_source_company_names")

BATCH_SIZE = 1000
SHOW_PER_SOURCE = 8


def planned_changes(db) -> dict:
    """{source name (or "(no source)"): Counter((old, new) -> jobs)}."""
    sources = {s.id: s for s in db.scalars(select(CrawlSource))}
    rows = db.execute(
        select(JobPostingUrl.crawl_source_id, JobPosting.page_company_name, JobPosting.company_name).join(
            JobPostingUrl, JobPostingUrl.id == JobPosting.url_id
        )
    ).all()
    changes: dict = defaultdict(Counter)
    for source_id, page, current in rows:
        source = sources.get(source_id)
        new = company_for(source, page if page is not None else current)
        if new and new != current:
            changes[source.name if source else "(no source)"][(current, new)] += 1
    return changes


def _apply_no_source(db) -> int:
    rows = db.execute(
        select(JobPosting.id, JobPosting.page_company_name, JobPosting.company_name)
        .join(JobPostingUrl, JobPostingUrl.id == JobPosting.url_id)
        .where(JobPostingUrl.crawl_source_id.is_(None))
    ).all()
    by_name: dict = defaultdict(list)
    for posting_id, page, current in rows:
        new = company_for(None, page if page is not None else current)
        if new and new != current:
            by_name[new].append(posting_id)
    for name, ids in by_name.items():
        for start in range(0, len(ids), BATCH_SIZE):
            db.execute(
                update(JobPosting)
                .where(JobPosting.id.in_(ids[start : start + BATCH_SIZE]))
                .values(company_name=name, company_key=normalize_company_name(name))
                .execution_options(synchronize_session=False)
            )
    return sum(len(ids) for ids in by_name.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Report the changes without writing.")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        changes = planned_changes(db)
        for source_name, counter in sorted(changes.items(), key=lambda kv: -sum(kv[1].values())):
            logger.info("%7d  %s", sum(counter.values()), source_name)
            for (old, new), n in counter.most_common(SHOW_PER_SOURCE):
                logger.info("         %6d  %r -> %r", n, old, new)
        total = sum(sum(c.values()) for c in changes.values())
        logger.info("%s %d job(s) across %d source(s).", "Would rename" if args.dry_run else "Renaming", total, len(changes))
        if args.dry_run:
            return

        renamed = 0
        for source in db.scalars(select(CrawlSource).order_by(CrawlSource.name)).all():
            renamed += apply_source_company(db, source)
            db.commit()
        renamed += _apply_no_source(db)
        db.commit()
        logger.info("Done: %d job(s) renamed.", renamed)
    finally:
        db.close()


if __name__ == "__main__":
    main()
