"""One-off backfill: give existing Tech postings their standard titles
(JobPosting.standard_titles) from the sector service, which returns them with
the sector in one call (see app.services.job_sector).

Going forward, every scan of a new or changed Tech posting gets its titles in
the same sector call. This brings existing rows in line: by default every
Tech posting without titles (~107k when this was written, ~94k of them live).
--all re-sends every Tech posting, e.g. after a new title model. Each posting
is sent as the scan sends it and stored with the same store_prediction, so it
also gets the sector the service gives today — the same model and text, so
the same sector; any change is counted and logged. scanned_at doesn't move,
so no alert emails and no page re-renders.

Calls the service --workers at a time (each sector instance handles one at a
time; ~0.3 s per Tech posting).

Use --dry-run --limit 50 first: it writes nothing and prints, for each posting,
the title and the titles it would get. --sample 50 does the same on a random
few.

Usage:
    python -m one_off.backfill_standard_titles --dry-run --limit 50   # review a few
    python -m one_off.backfill_standard_titles --sample 50            # review a random few
    python -m one_off.backfill_standard_titles                        # every Tech posting without titles
    python -m one_off.backfill_standard_titles --all                  # every Tech posting

Needs SECTOR_API_URL (and, locally, an identity token: `gcloud auth login`).
On prod, run it from the deployed image as a one-off Cloud Run job execution
(same image/secrets/env as `migrate`).
"""

import argparse
import collections
import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.enums import JobSector
from app.models.job_posting import JobPosting
from app.services.job_sector import input_hash, sector_for, store_prediction
from app.services.sector_api import classify_remote

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_standard_titles")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="Look at at most this many postings.")
    parser.add_argument("--batch-size", type=int, default=200, help="Postings per read/commit.")
    parser.add_argument("--workers", type=int, default=8, help="API calls in flight at once.")
    parser.add_argument("--all", action="store_true", help="Re-send every Tech posting, not just untagged ones.")
    parser.add_argument("--dry-run", action="store_true", help="Print what each posting would get; write nothing.")
    parser.add_argument(
        "--sample", type=int, default=None, help="Dry-run a random sample of this many postings (implies --dry-run)."
    )
    args = parser.parse_args()
    if args.sample:
        args.dry_run, args.limit = True, args.sample
    return args


def main() -> None:
    args = _parse_args()
    if not settings.sector_api_url:
        raise SystemExit("SECTOR_API_URL is not set.")

    db = SessionLocal()
    try:
        seen = tagged = sector_changed = failed = 0
        top: collections.Counter[str] = collections.Counter()
        last_id = None
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            while args.limit is None or seen < args.limit:
                # A random sample is one read: id order would show mostly the oldest employers.
                query = select(JobPosting).where(JobPosting.sector == JobSector.ENGINEERING_TECH)
                query = query.order_by(func.random() if args.sample else JobPosting.id)
                if not args.all:
                    query = query.where(JobPosting.standard_title_model.is_(None))
                if last_id is not None:
                    if args.sample:
                        break
                    query = query.where(JobPosting.id > last_id)
                size = args.batch_size if args.limit is None else min(args.batch_size, args.limit - seen)
                postings = db.scalars(query.limit(size)).all()
                if not postings:
                    break
                last_id = postings[-1].id
                predictions = list(pool.map(lambda p: classify_remote(p.title, p.description), postings))

                for posting, prediction in zip(postings, predictions):
                    if prediction is None:
                        failed += 1  # left as it was; a later run (or its next rescan) retries it
                        continue
                    new_sector = sector_for(prediction).value
                    if new_sector != posting.sector:
                        sector_changed += 1
                        logger.info("Sector change: %s | %s -> %s", posting.title, posting.sector, new_sector)
                    titles = prediction.standard_titles if new_sector == JobSector.ENGINEERING_TECH else None
                    if titles:
                        tagged += 1
                        top[titles[0]] += 1
                    if args.dry_run:
                        print(f"{(posting.title or '')[:60]:<60} | {(posting.company_name or '')[:25]:<25} | {', '.join(titles or [])}")
                    else:
                        store_prediction(posting, prediction, input_hash(posting.title, posting.description))
                seen += len(postings)
                if not args.dry_run:
                    db.commit()
                logger.info("%d seen, %d tagged, %d sector changes, %d API failures so far", seen, tagged, sector_changed, failed)

        verb = "would be tagged (dry-run, nothing written)" if args.dry_run else "tagged"
        logger.info(
            "Done. %d postings; %d %s; %d sector changes; %d API failures (left as they were).",
            seen, tagged, verb, sector_changed, failed,
        )
        logger.info("Top titles: %s", dict(top.most_common(20)))
    finally:
        db.close()


if __name__ == "__main__":
    main()
