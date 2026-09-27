"""One-off backfill: decode HTML entities in existing JobPosting.description text.

description is set going forward by _upsert_posting (app.services.jobs) via
decode_entities on every scan; this brings existing rows in line. A scraped
Workday description in particular can carry a literal "&#xa;" where a real
newline belongs — the browser does interpret that entity as a newline, but
normal HTML whitespace-collapsing then squashes it to a single space, so a
posting's structured "Date Posted:" / "Country:" / ... fields run together
into one unreadable line (verified live against a real RTX posting).

Pure text decoding, no network calls — safe and fast over the whole table.
Reads/writes only id/description (a large column on its own; the rest of
JobPosting is deliberately left out of the query, same reasoning as
backfill_metros.py's own id/locations/metros/workplace_type-only read),
walks the table in id order, and writes only rows whose description
actually changes, so it's idempotent.

Use --dry-run first: it writes nothing and logs how many rows would change.

Usage:
    python -m one_off.backfill_description_entities --dry-run
    python -m one_off.backfill_description_entities
    python -m one_off.backfill_description_entities --limit 2000 --dry-run

On prod, run it from the deployed image as a one-off Cloud Run job execution
(same image/secrets/env as `migrate`, command
`python -m one_off.backfill_description_entities`), same as backfill_metros/
backfill_sector/backfill_country.
"""

import argparse
import logging

from sqlalchemy import bindparam, select, update

from app.db.session import SessionLocal
from app.models.job_posting import JobPosting
from app.services.jobs import decode_entities

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_description_entities")


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
        .values(description=bindparam("new_description"))
    )

    db = SessionLocal()
    try:
        seen = changed = 0
        last_id = None
        while args.limit is None or seen < args.limit:
            query = select(JobPosting.id, JobPosting.description).order_by(JobPosting.id)
            if last_id is not None:
                query = query.where(JobPosting.id > last_id)
            batch_size = args.batch_size if args.limit is None else min(args.batch_size, args.limit - seen)
            rows = db.execute(query.limit(batch_size)).all()
            if not rows:
                break

            updates = []
            for row in rows:
                decoded = decode_entities(row.description) if row.description else row.description
                if decoded != row.description:
                    updates.append({"posting_id": row.id, "new_description": decoded})
            seen += len(rows)
            changed += len(updates)
            last_id = rows[-1].id

            if updates and not args.dry_run:
                db.execute(write, updates)
                db.commit()
            logger.info("%d postings seen, %d changes so far", seen, changed)

        verb = "would change (dry-run, nothing written)" if args.dry_run else "changed"
        logger.info("Done. %d postings seen; description %s on %d.", seen, verb, changed)
    finally:
        db.close()


if __name__ == "__main__":
    main()
