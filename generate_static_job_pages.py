"""Publishes the static, crawlable "jobs by sector, by day" pages — see
app.services.static_pages for the query/render/publish logic this just
drives. The job board at /jobs is a client-rendered React SPA (invisible to
search engines); these plain HTML pages, one per sector per US calendar day
(see static_pages.DAY_BOUNDARY_TZ), are published straight into the
frontend's S3 bucket/CloudFront distribution instead.

Regenerates **today's** pages by default, overwriting whatever was written
earlier today — meant to run every 30 minutes (its own independent
schedule, decoupled from crawl_dispatcher/crawl-worker: see the deploy notes
in deploy/gcloud-deploy.sh for why) so a day's page fills in as jobs are
found, then simply stops being touched once the day rolls over, freezing at
its last update as the permanent historical record. No separate
backfill/finalize step exists — --date lets you (re)generate an older day by
hand if you ever want to, but nothing does that automatically.

Sectors with zero jobs that day are skipped entirely (no thin/empty page).

Usage:
    python generate_static_job_pages.py                      # today (Pacific), publishes
    python generate_static_job_pages.py --date 2026-09-25    # a specific day
    python generate_static_job_pages.py --dry-run            # report only, no S3/CloudFront calls
"""

import argparse
import logging
from datetime import date, datetime

from app.core.log_config import configure_logging
from app.db.session import SessionLocal
from app.services.static_pages import DAY_BOUNDARY_TZ, generate_for_date

configure_logging()
logger = logging.getLogger("app.generate_static_job_pages")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD (Pacific). Defaults to today.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be written; no S3/CloudFront calls.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    target_date = date.fromisoformat(args.date) if args.date else datetime.now(DAY_BOUNDARY_TZ).date()

    db = SessionLocal()
    try:
        result = generate_for_date(db, target_date, dry_run=args.dry_run)
    finally:
        db.close()

    if not result.sector_job_counts:
        logger.info("No jobs found for any sector on %s; nothing published.", target_date.isoformat())
        return
    verb = "Would publish" if args.dry_run else "Published"
    for slug, count in sorted(result.sector_job_counts.items()):
        logger.info("%s %s/%s: %d job(s).", verb, slug, target_date.isoformat(), count)
    logger.info("%s %d sector page(s) for %s.", verb, len(result.sector_job_counts), target_date.isoformat())


def dispatch(_request=None) -> tuple[str, int]:
    """Cloud Functions (2nd gen) HTTP entry point — Cloud Scheduler calls this
    on its own 30-minute cadence instead of a shell invoking main() directly."""
    main()
    return "ok", 200


if __name__ == "__main__":
    main()
