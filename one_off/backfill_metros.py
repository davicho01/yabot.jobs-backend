"""One-off backfill: recompute the location-derived fields of existing postings.

  - JobPosting.metros — the metro/micro areas and states a posting's `locations`
    fall in (app.services.geo.resolve_area_codes).
  - JobPosting.places — the [lat, lon] of each city those locations name
    (app.services.geo.resolve_places), what "within N miles of <city>" searches measure.
  - JobPosting.workplace_type — what the location text says about remote / hybrid /
    on-site, reconciled with what the adapter recorded (app.services.workplace).
  - JobPosting.title / company_name / location — HTML entities decoded
    ("Sales &amp; Marketing" -> "Sales & Marketing") for postings scanned before that
    was done on the way in, and JobPosting.locations re-split from the decoded
    location ("Tacoma &amp; Gordon" used to be cut in two at the ";").

All of these are set going forward by _upsert_posting on every scan; this brings existing
rows in line, and can be re-run whenever the resolver rules or the bundled data
under app/data/geo/ change (deploys don't touch existing rows). The resolver is
pure lookups against bundled data — no network calls — so it's safe and fast over
the whole table. Reads only id/locations/metros/workplace_type (rows are large:
descriptions live in the same table), walks the table in id order, and writes only
rows whose values actually change, so it's idempotent.

Use --dry-run first: it writes nothing and logs how many rows would change,
including a work-type transition matrix (e.g. "onsite -> remote: 1,234") to review.

Usage:
    python -m one_off.backfill_metros --dry-run                # report only
    python -m one_off.backfill_metros                          # areas + work type, every posting
    python -m one_off.backfill_metros --skip-workplace         # areas only
    python -m one_off.backfill_metros --limit 2000 --dry-run

On prod, run it from the deployed image as a one-off Cloud Run job execution
(the `backfill-metros` job: same image/secrets/env as `migrate`, command
`python backfill_metros.py`; point it at the new image first).
"""

import argparse
import collections
import logging
import re

from sqlalchemy import bindparam, select, update

from app.db.session import SessionLocal
from app.models.job_posting import JobPosting
from app.services.geo import resolve_area_codes, resolve_places
from app.services.job_locations import split_locations
from app.services.jobs import decode_entities
from app.services.workplace import infer_workplace_type, reconcile_workplace_type

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_metros")

_HTML_ENTITY_RE = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);")


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
    # Core (table-level) UPDATE: an ORM-enabled one with extra WHERE criteria
    # can't be used with executemany.
    write = (
        update(table)
        .where(table.c.id == bindparam("posting_id"))
        .values(
            title=bindparam("new_title"),
            company_name=bindparam("new_company_name"),
            location=bindparam("new_location"),
            locations=bindparam("new_locations"),
            metros=bindparam("new_metros"),
            places=bindparam("new_places"),
            workplace_type=bindparam("new_workplace_type"),
        )
    )

    db = SessionLocal()
    try:
        seen = areas_changed = places_changed = locations_repaired = text_decoded = with_area = with_places = 0
        workplace_transitions: collections.Counter[str] = collections.Counter()
        last_id = None
        while args.limit is None or seen < args.limit:
            query = select(
                JobPosting.id,
                JobPosting.title,
                JobPosting.company_name,
                JobPosting.location,
                JobPosting.locations,
                JobPosting.metros,
                JobPosting.places,
                JobPosting.workplace_type,
            ).order_by(JobPosting.id)
            if last_id is not None:
                query = query.where(JobPosting.id > last_id)
            batch_size = args.batch_size if args.limit is None else min(args.batch_size, args.limit - seen)
            rows = db.execute(query.limit(batch_size)).all()
            if not rows:
                break

            updates = []
            for row in rows:
                title, company_name, location = (
                    decode_entities(row.title) if row.title and _HTML_ENTITY_RE.search(row.title) else row.title,
                    decode_entities(row.company_name)
                    if row.company_name and _HTML_ENTITY_RE.search(row.company_name)
                    else row.company_name,
                    decode_entities(row.location)
                    if row.location and _HTML_ENTITY_RE.search(row.location)
                    else row.location,
                )
                text_decoded += (title, company_name, location) != (row.title, row.company_name, row.location)

                locations = row.locations
                if location != row.location:
                    # Split before entities were decoded: "Tacoma &amp; Gordon" became
                    # two bogus entries at the ";" — re-split from the decoded text.
                    locations = split_locations(location)
                locations_repaired += locations != row.locations

                areas = resolve_area_codes(locations)
                with_area += bool(areas)
                areas_changed += areas != row.metros

                places = resolve_places(locations)
                with_places += bool(places)
                places_changed += places != row.places

                workplace = row.workplace_type
                if not args.skip_workplace:
                    workplace = reconcile_workplace_type(row.workplace_type, infer_workplace_type(locations))
                if workplace != row.workplace_type:
                    workplace_transitions[f"{row.workplace_type} -> {workplace}"] += 1

                if (
                    (title, company_name, location) != (row.title, row.company_name, row.location)
                    or locations != row.locations
                    or areas != row.metros
                    or places != row.places
                    or workplace != row.workplace_type
                ):
                    updates.append(
                        {
                            "posting_id": row.id,
                            "new_title": title,
                            "new_company_name": company_name,
                            "new_location": location,
                            "new_locations": locations,
                            "new_metros": areas,
                            "new_places": places,
                            "new_workplace_type": str(workplace),
                        }
                    )
            seen += len(rows)
            last_id = rows[-1].id

            if updates and not args.dry_run:
                db.execute(write, updates)
                db.commit()
            logger.info(
                "%d postings seen, %d area changes, %d work-type changes so far",
                seen,
                areas_changed,
                sum(workplace_transitions.values()),
            )

        verb = "would change (dry-run, nothing written)" if args.dry_run else "changed"
        logger.info("Done. %d postings; areas %s on %d; %d have at least one area.", seen, verb, areas_changed, with_area)
        logger.info("Places %s on %d; %d have at least one city with coordinates.", verb, places_changed, with_places)
        logger.info("Title/company/location text had HTML entities decoded on %d postings.", text_decoded)
        logger.info("Location entries re-split on %d postings.", locations_repaired)
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
