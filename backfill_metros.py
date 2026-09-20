"""One-off backfill: populate JobPosting.metros for existing rows that predate
that column (it's set going forward by _upsert_posting, on every scan).

Each posting's `locations` list is resolved to Census metro/micro area codes by
app.services.geo — pure lookups against bundled data, no network calls — so
this is safe and fast over the whole table. Reads only id/locations/metros (the
rows are large: descriptions live in the same table), walks the table in id
order, and writes only rows whose value actually changes, so it's idempotent and
can be re-run — e.g. after build_geo_data.py or the resolver rules change.

Until it has run, existing postings have an empty `metros` and the area filter
under-reports; new and rescanned postings are correct immediately.

Usage:
    python backfill_metros.py                   # every posting
    python backfill_metros.py --limit 2000 --dry-run

On prod, run it from the deployed image as a one-off Cloud Run job execution
(same image/secrets as the `migrate` job, command `python backfill_metros.py`).
"""

import argparse
import logging

from sqlalchemy import bindparam, select, update

from app.db.session import SessionLocal
from app.models.job_posting import JobPosting
from app.services.geo import resolve_metros

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_metros")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="Look at at most this many postings.")
    parser.add_argument("--batch-size", type=int, default=1000, help="Postings read/written per round trip.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing anything.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    # Core (table-level) UPDATE: an ORM-enabled one with extra WHERE criteria
    # can't be used with executemany.
    write = (
        update(JobPosting.__table__)
        .where(JobPosting.__table__.c.id == bindparam("posting_id"))
        .values(metros=bindparam("new_metros"))
    )

    db = SessionLocal()
    try:
        seen = changed = with_metro = 0
        last_id = None
        while args.limit is None or seen < args.limit:
            query = select(JobPosting.id, JobPosting.locations, JobPosting.metros).order_by(JobPosting.id)
            if last_id is not None:
                query = query.where(JobPosting.id > last_id)
            batch_size = args.batch_size if args.limit is None else min(args.batch_size, args.limit - seen)
            rows = db.execute(query.limit(batch_size)).all()
            if not rows:
                break

            updates = []
            for row in rows:
                metros = resolve_metros(row.locations)
                with_metro += bool(metros)
                if metros != row.metros:
                    updates.append({"posting_id": row.id, "new_metros": metros})
            seen += len(rows)
            last_id = rows[-1].id
            changed += len(updates)

            if updates and not args.dry_run:
                db.execute(write, updates)
                db.commit()
            logger.info("%d postings seen, %d changed so far, %d have a metro area", seen, changed, with_metro)

        logger.info(
            "Done. %d postings, %d %s, %d with at least one metro area.",
            seen,
            changed,
            "would change (dry-run, nothing written)" if args.dry_run else "updated",
            with_metro,
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
