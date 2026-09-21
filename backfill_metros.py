"""One-off backfill: recompute the location-derived fields of existing postings.

  - JobPosting.metros — the metro/micro areas and states a posting's `locations`
    fall in (app.services.geo.resolve_area_codes).
  - JobPosting.workplace_type — what the location text says about remote / hybrid /
    on-site, reconciled with what the adapter recorded (app.services.workplace).

Both are set going forward by _upsert_posting on every scan; this brings existing
rows in line, and can be re-run whenever the resolver rules or the bundled data
under app/data/geo/ change (deploys don't touch existing rows). The resolver is
pure lookups against bundled data — no network calls — so it's safe and fast over
the whole table. Reads only id/locations/metros/workplace_type (rows are large:
descriptions live in the same table), walks the table in id order, and writes only
rows whose values actually change, so it's idempotent.

Use --dry-run first: it writes nothing and logs how many rows would change,
including a work-type transition matrix (e.g. "onsite -> remote: 1,234") to review.

Usage:
    python backfill_metros.py --dry-run                # report only
    python backfill_metros.py                          # areas + work type, every posting
    python backfill_metros.py --skip-workplace         # areas only
    python backfill_metros.py --limit 2000 --dry-run

On prod, run it from the deployed image as a one-off Cloud Run job execution
(the `backfill-metros` job: same image/secrets/env as `migrate`, command
`python backfill_metros.py`; point it at the new image first).
"""

import argparse
import collections
import logging

from sqlalchemy import bindparam, select, update

from app.db.session import SessionLocal
from app.models.job_posting import JobPosting
from app.services.geo import resolve_area_codes
from app.services.workplace import infer_workplace_type, reconcile_workplace_type

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_metros")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="Look at at most this many postings.")
    parser.add_argument("--batch-size", type=int, default=1000, help="Postings read/written per round trip.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing anything.")
    parser.add_argument(
        "--skip-workplace", action="store_true", help="Only recompute the areas; leave workplace_type alone."
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    table = JobPosting.__table__
    # Core (table-level) UPDATEs: an ORM-enabled one with extra WHERE criteria
    # can't be used with executemany.
    write_areas = update(table).where(table.c.id == bindparam("posting_id")).values(metros=bindparam("new_metros"))
    write_both = (
        update(table)
        .where(table.c.id == bindparam("posting_id"))
        .values(metros=bindparam("new_metros"), workplace_type=bindparam("new_workplace_type"))
    )

    db = SessionLocal()
    try:
        seen = areas_changed = with_area = 0
        workplace_transitions: collections.Counter[str] = collections.Counter()
        last_id = None
        while args.limit is None or seen < args.limit:
            query = select(JobPosting.id, JobPosting.locations, JobPosting.metros, JobPosting.workplace_type).order_by(
                JobPosting.id
            )
            if last_id is not None:
                query = query.where(JobPosting.id > last_id)
            batch_size = args.batch_size if args.limit is None else min(args.batch_size, args.limit - seen)
            rows = db.execute(query.limit(batch_size)).all()
            if not rows:
                break

            area_updates, both_updates = [], []
            for row in rows:
                areas = resolve_area_codes(row.locations)
                with_area += bool(areas)
                areas_differ = areas != row.metros
                areas_changed += areas_differ

                workplace = row.workplace_type
                if not args.skip_workplace:
                    workplace = reconcile_workplace_type(row.workplace_type, infer_workplace_type(row.locations))
                if workplace != row.workplace_type:
                    workplace_transitions[f"{row.workplace_type} -> {workplace}"] += 1
                    both_updates.append(
                        {"posting_id": row.id, "new_metros": areas, "new_workplace_type": str(workplace)}
                    )
                elif areas_differ:
                    area_updates.append({"posting_id": row.id, "new_metros": areas})
            seen += len(rows)
            last_id = rows[-1].id

            if not args.dry_run:
                if area_updates:
                    db.execute(write_areas, area_updates)
                if both_updates:
                    db.execute(write_both, both_updates)
                if area_updates or both_updates:
                    db.commit()
            logger.info(
                "%d postings seen, %d area changes, %d work-type changes so far",
                seen,
                areas_changed,
                sum(workplace_transitions.values()),
            )

        verb = "would change (dry-run, nothing written)" if args.dry_run else "changed"
        logger.info("Done. %d postings; areas %s on %d; %d have at least one area.", seen, verb, areas_changed, with_area)
        if args.skip_workplace:
            logger.info("Work type left alone (--skip-workplace).")
        else:
            logger.info("Work type %s on %d postings:", verb, sum(workplace_transitions.values()))
            for transition, count in workplace_transitions.most_common():
                logger.info("    %8d  %s", count, transition)
    finally:
        db.close()


if __name__ == "__main__":
    main()
