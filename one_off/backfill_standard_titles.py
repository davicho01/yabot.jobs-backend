"""One-off backfill: tag existing Tech postings with their standard titles
(JobPosting.standard_titles) using the in-process model (app.services.standard_titles).

Going forward, _upsert_posting tags every Tech posting on each scan (see
app.services.job_standard_titles). This brings existing rows in line: by
default every Tech posting not yet tagged (~107k when this was written, ~94k
of them live). --all re-tags every Tech posting, e.g. after a new model
version. Only the three standard-title columns are written: scanned_at
doesn't move, so no alert emails and no page re-renders.

Use --dry-run --limit 50 first: it writes nothing and prints, for each posting,
the title and the titles it would get. --sample 50 does the same on a random
few.

Runs in parallel as a Cloud Run job with --tasks=N: each task takes the
postings whose id falls in its share (CLOUD_RUN_TASK_INDEX of
CLOUD_RUN_TASK_COUNT), so tasks never overlap.

Usage:
    python -m one_off.backfill_standard_titles --dry-run --limit 50   # review a few
    python -m one_off.backfill_standard_titles --sample 50            # review a random few
    python -m one_off.backfill_standard_titles                        # every untagged Tech posting
    python -m one_off.backfill_standard_titles --all                  # every Tech posting

Needs the model (STANDARD_TITLE_MODEL: gs://... on prod, or
one_off/fetch_standard_title_model.sh locally).
"""

import argparse
import collections
import logging
import os

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.enums import JobSector
from app.models.job_posting import JobPosting
from app.services import standard_titles
from app.services.job_sector import input_hash
from app.services.job_standard_titles import store_titles

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_standard_titles")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="Look at at most this many postings.")
    parser.add_argument("--batch-size", type=int, default=200, help="Postings per read/commit.")
    parser.add_argument("--all", action="store_true", help="Re-tag every Tech posting, not just untagged ones.")
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
    version = standard_titles.model_version()
    if version is None:
        raise SystemExit("Standard-title model not available (STANDARD_TITLE_MODEL).")
    task, tasks = int(os.environ.get("CLOUD_RUN_TASK_INDEX", 0)), int(os.environ.get("CLOUD_RUN_TASK_COUNT", 1))
    logger.info("Model %s; task %d of %d.", version, task + 1, tasks)

    db = SessionLocal()
    try:
        seen = failed = 0
        counts: collections.Counter[str] = collections.Counter()
        last_id = None
        while args.limit is None or seen < args.limit:
            # A random sample is one read: id order would show mostly the oldest employers.
            query = select(JobPosting).where(JobPosting.sector == JobSector.ENGINEERING_TECH)
            query = query.order_by(func.random() if args.sample else JobPosting.id)
            if not args.all:
                query = query.where(JobPosting.standard_title_model.is_(None))
            if tasks > 1:
                query = query.where(JobPosting.id % tasks == task)
            if last_id is not None:
                if args.sample:
                    break
                query = query.where(JobPosting.id > last_id)
            size = args.batch_size if args.limit is None else min(args.batch_size, args.limit - seen)
            postings = db.scalars(query.limit(size)).all()
            if not postings:
                break
            last_id = postings[-1].id

            for posting in postings:
                titles = standard_titles.tag_posting(posting.title, posting.description)
                if titles is None:
                    failed += 1
                    continue
                counts.update(titles[:1])
                if args.dry_run:
                    print(f"{(posting.title or '')[:60]:<60} | {(posting.company_name or '')[:25]:<25} | {', '.join(titles)}")
                else:
                    store_titles(posting, titles, input_hash(posting.title, posting.description))
            seen += len(postings)
            if not args.dry_run:
                db.commit()
            logger.info("%d postings seen, %d model failures so far", seen, failed)

        verb = "would be tagged (dry-run, nothing written)" if args.dry_run else "tagged"
        logger.info("Done. %d postings %s; %d model failures (left untagged).", seen - failed, verb, failed)
        logger.info("Top titles: %s", dict(counts.most_common(20)))
    finally:
        db.close()


if __name__ == "__main__":
    main()
