"""One-off backfill: compute JobPosting.country for existing postings.

country is set going forward by _upsert_posting (app.services.jobs) on every
scan/rescan, via app.services.geo.resolve_country_for_locations. This brings
existing rows in line — needed before the per-country static pages
(app.services.static_pages) can filter on it, since every row scanned before
this column existed currently sits at country=NULL, which the filter would
otherwise (correctly, but unhelpfully) treat as "not US."

Reads only id/locations/country (rows are large: descriptions live in the
same table), walks the table in id order, and writes only rows whose value
actually changed, so it's idempotent — safe to re-run any time the resolver
in geo.py changes.

Use --dry-run first: it writes nothing and logs a country -> count breakdown
to review before committing to the real run.

Usage:
    python -m one_off.backfill_country --dry-run                # report only
    python -m one_off.backfill_country                          # write
    python -m one_off.backfill_country --limit 2000 --dry-run

On prod, run it from the deployed image as a one-off Cloud Run job execution
(the `backfill-country` job: same image/secrets/env as `migrate`, command
`python -m one_off.backfill_country`), same as backfill-metros.
"""

import argparse
import collections
import logging

from sqlalchemy import bindparam, select, update

from app.db.session import SessionLocal
from app.models.job_posting import JobPosting
from app.services.geo import resolve_country_for_locations

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_country")


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
        .values(country=bindparam("new_country"))
    )

    db = SessionLocal()
    try:
        seen = changed = 0
        country_counts: collections.Counter[str] = collections.Counter()
        last_id = None
        while args.limit is None or seen < args.limit:
            query = select(JobPosting.id, JobPosting.locations, JobPosting.country).order_by(JobPosting.id)
            if last_id is not None:
                query = query.where(JobPosting.id > last_id)
            batch_size = args.batch_size if args.limit is None else min(args.batch_size, args.limit - seen)
            rows = db.execute(query.limit(batch_size)).all()
            if not rows:
                break

            updates = []
            for row in rows:
                country = resolve_country_for_locations(row.locations)
                country_counts[country or "unknown"] += 1
                if country != row.country:
                    updates.append({"posting_id": row.id, "new_country": country})
            seen += len(rows)
            changed += len(updates)
            last_id = rows[-1].id

            if updates and not args.dry_run:
                db.execute(write, updates)
                db.commit()
            logger.info("%d postings seen, %d changes so far", seen, changed)

        verb = "would change (dry-run, nothing written)" if args.dry_run else "changed"
        logger.info("Done. %d postings seen; country %s on %d.", seen, verb, changed)
        for country, count in country_counts.most_common():
            logger.info("    %8d  %s", count, country)
    finally:
        db.close()


if __name__ == "__main__":
    main()
