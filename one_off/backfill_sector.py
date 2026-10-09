"""One-off backfill: classify JobPosting.sector for existing postings with the
sector model service (app.services.sector_api).

Going forward, _upsert_posting sets the sector on every scan (see
app.services.job_sector). This brings existing rows in line: by default every
posting not yet classified by a model — still on the old keyword lists
(sector_model = 'keyword-matcher') or pending (NULL). --all reclassifies every
posting, e.g. after a new model version. Walks the table in id order, reads
only id/title/description/sector, and calls the API --workers at a time
(each instance handles one at a time; ~0.6 s per posting).

Use --dry-run --limit 50 first: it writes nothing and prints, for each posting,
the title, company, current sector and the model's sector + confidence.

Usage:
    python -m one_off.backfill_sector --dry-run --limit 50   # review a few
    python -m one_off.backfill_sector --sample 50            # review a random few
    python -m one_off.backfill_sector                        # everything not yet model-classified
    python -m one_off.backfill_sector --all                  # every posting

Needs SECTOR_API_URL (and, locally, an identity token: `gcloud auth login`).
On prod, run it from the deployed image as a one-off Cloud Run job execution
(same image/secrets/env as `migrate`).
"""

import argparse
import collections
import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, or_, select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.job_posting import JobPosting
from app.services.job_sector import input_hash, sector_for, store_prediction
from app.services.sector_api import classify_remote

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_sector")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="Look at at most this many postings.")
    parser.add_argument("--batch-size", type=int, default=200, help="Postings per read/commit.")
    parser.add_argument("--workers", type=int, default=4, help="API calls in flight at once.")
    parser.add_argument("--all", action="store_true", help="Reclassify every posting, not just unclassified ones.")
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
        seen = changed = failed = 0
        before: collections.Counter[str] = collections.Counter()
        after: collections.Counter[str] = collections.Counter()
        last_id = None
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            while args.limit is None or seen < args.limit:
                # A random sample is one read: id order would show mostly the oldest employers.
                query = select(JobPosting).order_by(func.random() if args.sample else JobPosting.id)
                if not args.all:
                    query = query.where(or_(JobPosting.sector_model.is_(None), JobPosting.sector_model == "keyword-matcher"))
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
                    before[posting.sector] += 1
                    if prediction is None:
                        failed += 1
                        after[posting.sector] += 1
                        continue
                    new_sector = sector_for(prediction).value
                    after[new_sector] += 1
                    changed += new_sector != posting.sector
                    if args.dry_run:
                        print(
                            f"{(posting.title or '')[:55]:<55} | {(posting.company_name or '')[:25]:<25} | "
                            f"{posting.sector:<24} -> {new_sector:<24} {prediction.confidence:.2f}"
                        )
                    else:
                        store_prediction(posting, prediction, input_hash(posting.title, posting.description))
                seen += len(postings)
                if not args.dry_run:
                    db.commit()
                logger.info("%d postings seen, %d sector changes, %d API failures so far", seen, changed, failed)

        verb = "would change (dry-run, nothing written)" if args.dry_run else "changed"
        logger.info("Done. %d postings; sector %s on %d; %d API failures (left as they were).", seen, verb, changed, failed)
        logger.info("Before: %s", dict(before.most_common()))
        logger.info("After:  %s", dict(after.most_common()))
    finally:
        db.close()


if __name__ == "__main__":
    main()
