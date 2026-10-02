"""Publishes the static, crawlable "jobs by country, sector, and day" pages
(/jobs/{country-slug}/{sector-slug}/{date}) — see app.services.static_pages
for the query/render/publish logic this just drives. The job board at /jobs
is a client-rendered React SPA (invisible to search engines); these plain
HTML pages, one per country x sector per US calendar day (see
static_pages.DAY_BOUNDARY_TZ), are published straight into the frontend's S3
bucket/CloudFront distribution instead. Just the US for now
(static_pages._COUNTRIES) — JobPosting.country is set at scan time (see
app.services.geo.resolve_country_for_locations), so adding a country here is
just adding a row to that list, no other change needed.

Regenerates **today's** pages by default, overwriting whatever was written
earlier today — runs twice a day (2pm/10pm ET, an hour after each
crawl-dispatch; see deploy/gcloud-deploy.sh §10 for why not more often) so
a day's page fills in as jobs are found, then simply stops being touched
once the day rolls over, freezing at its last update as the permanent
historical record. No separate backfill/finalize step exists for day
pages — --date lets you (re)generate an older day by
hand if you ever want to, but nothing does that automatically.

"Today's page" means jobs the employer actually posted today (falling back
to when we scanned it, for the — common — case where a posting has no
stated date at all — see app.services.static_pages.jobs_for_sector_day).
A job with a *known* posted_at from an earlier day belongs on that day's
page even if we only discover it today; since only today ever regenerates,
a stale-by-the-time-we-find-it job like that — whose own day's page has
already frozen — won't end up on any page. Accepted trade-off, same
"forward-only" posture as everything else here.

Sectors with zero jobs that day are skipped entirely (no thin/empty page).

Each run also brings the per-job pages (/job/{url_id}, see
app.services.static_job_pages) up to date — new jobs, rescanned ones, and
"no longer available" pages for jobs that closed — and then sends a single
CloudFront invalidation covering everything both passes changed.

Writes to SEO_PAGES_BUCKET in prod, or SEO_PAGES_OUTPUT_DIR in local dev
(see app.services.page_store); with neither set, only --dry-run works.

Usage:
    python generate_static_job_pages.py                      # today (Pacific), publishes
    python generate_static_job_pages.py --date 2026-09-25    # a specific day
    python generate_static_job_pages.py --dry-run            # report only, no S3/CloudFront calls
    python generate_static_job_pages.py --full               # also re-render every per-job page (backfill)
"""

import argparse
import logging
from datetime import date, datetime

from app.core.log_config import configure_logging
from app.db.session import SessionLocal
from app.services.static_job_pages import generate_job_pages
from app.services.static_pages import DAY_BOUNDARY_TZ, collapse_invalidation_paths, generate_for_date, invalidate_paths

configure_logging()
logger = logging.getLogger("app.generate_static_job_pages")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD (Pacific). Defaults to today.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be written; no S3/CloudFront calls.")
    parser.add_argument("--full", action="store_true", help="Re-render every per-job page, not just new/changed ones.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    target_date = date.fromisoformat(args.date) if args.date else datetime.now(DAY_BOUNDARY_TZ).date()

    db = SessionLocal()
    try:
        result = generate_for_date(db, target_date, dry_run=args.dry_run, invalidate=False)
        job_result = generate_job_pages(db, dry_run=args.dry_run, full=args.full)
    finally:
        db.close()

    verb = "Would publish" if args.dry_run else "Published"
    if not result.job_counts:
        logger.info("No jobs found for any country/sector on %s; no day pages written.", target_date.isoformat())
    for key, count in sorted(result.job_counts.items()):
        logger.info("%s jobs/%s/%s: %d job(s).", verb, key, target_date.isoformat(), count)
    if result.job_counts:
        logger.info("%s %d country/sector page(s) for %s.", verb, len(result.job_counts), target_date.isoformat())
    logger.info(
        "%s job pages: %d new, %d re-rendered, %d marked no longer available, %d unchanged.",
        verb, job_result.published, job_result.updated, job_result.removed, job_result.unchanged,
    )

    paths = collapse_invalidation_paths(result.touched_paths + job_result.touched_paths)
    if not args.dry_run:
        invalidate_paths(paths)


def dispatch(_request=None) -> tuple[str, int]:
    """Cloud Functions (2nd gen) HTTP entry point — Cloud Scheduler calls this
    on its own 30-minute cadence instead of a shell invoking main() directly."""
    main()
    return "ok", 200


if __name__ == "__main__":
    main()
