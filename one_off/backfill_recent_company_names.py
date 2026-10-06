"""One-off validation: apply the crawl-source company naming
(app.services.company_names.company_for) to the jobs first found in the
last few days only, to check the new rule on real data before it reaches
older jobs naturally. Everything else applies to jobs scanned from now on;
this deliberately touches a small, recent slice and nothing older.

For each recent job whose name would change it saves the current name as
page_company_name (when that's still empty — the page's raw name, kept for
sub-brand matching later) and sets company_name/company_key, in small
committed batches so it never holds a long lock. The dry run lists every
old -> new change with counts; review it before writing.

The jobs' static pages aren't re-rendered by this; the company hubs pick the
new names up on the next page run, and the app shows them at once.

Usage:
    python -m one_off.backfill_recent_company_names --dry-run            # last 2 days
    python -m one_off.backfill_recent_company_names --days 3 --dry-run
    python -m one_off.backfill_recent_company_names --days 2
"""

import argparse
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import bindparam, select, update

from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services.company_names import company_for
from app.services.job_dedup import normalize_company_name

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("app.backfill_recent_company_names")

BATCH_SIZE = 500


def planned_changes(db, since: datetime) -> list[tuple]:
    """[(posting_id, current name, new name, page name to keep)] for jobs first
    found since `since` whose name the new rule changes."""
    sources = {s.id: s for s in db.scalars(select(CrawlSource))}
    rows = db.execute(
        select(JobPosting.id, JobPostingUrl.crawl_source_id, JobPosting.page_company_name, JobPosting.company_name)
        .join(JobPostingUrl, JobPostingUrl.id == JobPosting.url_id)
        .where(JobPostingUrl.created_at >= since)
    ).all()
    changes = []
    for posting_id, source_id, page_name, current in rows:
        raw = page_name if page_name is not None else current
        new = company_for(sources.get(source_id), raw)
        if new and new != current:
            changes.append((posting_id, current, new, raw))
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=float, default=2, help="Only jobs first found in the last N days (default 2).")
    parser.add_argument("--dry-run", action="store_true", help="Report the changes without writing.")
    args = parser.parse_args()
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    db = SessionLocal()
    try:
        changes = planned_changes(db, since)
        for (old, new), n in Counter((old, new) for _, old, new, _ in changes).most_common():
            logger.info("%6d  %r -> %r", n, old, new)
        logger.info(
            "%s %d job(s) first found since %s.",
            "Would rename" if args.dry_run else "Renaming", len(changes), since.isoformat(timespec="minutes"),
        )
        if args.dry_run or not changes:
            return

        table = JobPosting.__table__
        write = (
            update(table)
            .where(table.c.id == bindparam("posting_id"))
            .values(
                company_name=bindparam("new_name"),
                company_key=bindparam("new_key"),
                page_company_name=bindparam("page_name"),
            )
        )
        params = [
            {"posting_id": pid, "new_name": new, "new_key": normalize_company_name(new), "page_name": raw}
            for pid, _, new, raw in changes
        ]
        for start in range(0, len(params), BATCH_SIZE):
            db.execute(write, params[start : start + BATCH_SIZE])
            db.commit()
        logger.info("Done.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
