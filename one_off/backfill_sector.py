"""One-off backfill: classify JobPosting.sector for existing postings.

Sector (the job function/department a posting is hiring for — engineering,
sales, service_trades, ...) is set going forward by _upsert_posting on every
scan via app.services.job_sector.classify_sector; this brings existing rows
in line. Runs the local sector model (app/ml/sector_model.npz) on each
title/description — no network calls, ~1.5 ms per posting, so it's safe and
fast over the whole table. Reads only id/title/description (rows are large:
descriptions live in the same table), walks the table in id order, and writes
only rows whose sector actually changes, so it's idempotent and safe to re-run
whenever the model file changes.

Use --dry-run first: it writes nothing and logs a before/after breakdown by
sector to review.

Usage:
    python -m one_off.backfill_sector --dry-run                # report only
    python -m one_off.backfill_sector                          # classify every posting
    python -m one_off.backfill_sector --limit 2000 --dry-run

On prod, run it from the deployed image as a one-off Cloud Run job execution
(same image/secrets/env as `migrate`, command `python backfill_sector.py`).
"""

import argparse
import collections
import logging

from sqlalchemy import bindparam, select, update

from app.db.session import SessionLocal
from app.models.job_posting import JobPosting
from app.services.job_sector import classify_sector

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_sector")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="Look at at most this many postings.")
    parser.add_argument("--batch-size", type=int, default=1000, help="Postings read/written per round trip.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing anything.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    table = JobPosting.__table__
    write = (
        update(table)
        .where(table.c.id == bindparam("posting_id"))
        .values(sector=bindparam("new_sector"))
    )

    db = SessionLocal()
    try:
        seen = changed = 0
        before_counts: collections.Counter[str] = collections.Counter()
        after_counts: collections.Counter[str] = collections.Counter()
        last_id = None
        while args.limit is None or seen < args.limit:
            query = select(JobPosting.id, JobPosting.title, JobPosting.description, JobPosting.sector).order_by(
                JobPosting.id
            )
            if last_id is not None:
                query = query.where(JobPosting.id > last_id)
            batch_size = args.batch_size if args.limit is None else min(args.batch_size, args.limit - seen)
            rows = db.execute(query.limit(batch_size)).all()
            if not rows:
                break

            updates = []
            for row in rows:
                before_counts[row.sector] += 1
                sector = str(classify_sector(row.title, row.description))
                after_counts[sector] += 1
                if sector != row.sector:
                    changed += 1
                    updates.append({"posting_id": row.id, "new_sector": sector})
            seen += len(rows)
            last_id = rows[-1].id

            if updates and not args.dry_run:
                db.execute(write, updates)
                db.commit()
            logger.info("%d postings seen, %d sector changes so far", seen, changed)

        verb = "would change (dry-run, nothing written)" if args.dry_run else "changed"
        logger.info("Done. %d postings; sector %s on %d.", seen, verb, changed)
        logger.info("Before: %s", dict(before_counts.most_common()))
        logger.info("After:  %s", dict(after_counts.most_common()))
    finally:
        db.close()


if __name__ == "__main__":
    main()
