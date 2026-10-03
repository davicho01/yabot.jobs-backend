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

Each run first brings the per-job pages up to date (see
app.services.static_job_pages: new jobs at /jobs/us/<sector>/<day>/<title>-
<place>-<id>, older ones staying at /job/<id>; rescanned ones re-rendered;
"no longer available" pages for jobs that closed) along with the company and
location hubs (app.services.static_hub_pages), then the day pages, which link
each job at its page's path — and sends a single CloudFront invalidation
covering everything both passes changed.

Before any of that, each run crawls logos for companies that need one (see
app.services.company_logos.sync_company_logos, capped at a couple of
minutes), so the pages rendered right after already show them.

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
from app.services.company_logos import sync_company_logos
from app.services.static_job_pages import JobPagesResult, generate_job_pages, read_job_paths
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
    job_error: Exception | None = None
    try:
        # Logos first, so the pages rendered below already carry any just
        # fetched (see sync_company_logos). Never allowed to cost the pages.
        if not args.dry_run:
            try:
                sync_company_logos(db)
            except Exception:
                db.rollback()
                logger.exception("Company logo sync failed; publishing pages without the new logos.")
        # Job pages first: the day pages link each job at the path its page
        # was published under (static_job_pages), and the country page lists
        # the top company/location hubs this pass builds. Isolated so a
        # failure here still lets the day pages go out (linking from the
        # last saved manifest) and get their CloudFront invalidation below.
        try:
            job_result = generate_job_pages(db, dry_run=args.dry_run, full=args.full)
            job_paths, hub_links = job_result.job_paths, job_result.country_links
        except Exception as exc:
            db.rollback()
            logger.exception("Per-job pages failed; the day pages are still published and invalidated.")
            job_result, job_error = JobPagesResult(), exc
            job_paths, hub_links = (read_job_paths() if not args.dry_run else {}), None
        result = generate_for_date(
            db, target_date, dry_run=args.dry_run, invalidate=False, job_paths=job_paths, hub_links=hub_links
        )
    finally:
        db.close()

    verb = "Would publish" if args.dry_run else "Published"
    if not result.job_counts:
        logger.info("No jobs found for any country/sector on %s; no day pages written.", target_date.isoformat())
    for key, count in sorted(result.job_counts.items()):
        logger.info("%s jobs/%s/%s: %d job(s).", verb, key, target_date.isoformat(), count)
    if result.job_counts:
        logger.info("%s %d country/sector page(s) for %s.", verb, len(result.job_counts), target_date.isoformat())
    if job_error is None:
        logger.info(
            "%s job pages: %d new, %d re-rendered, %d refreshed, %d marked no longer available, %d unchanged, "
            "%d deferred; %d hub page(s).",
            verb, job_result.published, job_result.updated, job_result.refreshed, job_result.removed,
            job_result.unchanged, job_result.deferred, job_result.hubs,
        )

    if not args.dry_run:
        invalidate_paths(collapse_invalidation_paths(result.touched_paths + job_result.touched_paths))
    if job_error is not None:
        raise job_error  # still report the run as failed, after the day pages are fully out


def dispatch(_request=None) -> tuple[str, int]:
    """Cloud Functions (2nd gen) HTTP entry point — Cloud Scheduler calls this
    on its own 30-minute cadence instead of a shell invoking main() directly."""
    main()
    return "ok", 200


if __name__ == "__main__":
    main()
