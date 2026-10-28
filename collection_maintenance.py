"""Nightly upkeep of the job-collection registry (app.models.job_collection),
before the nightly page refresh: stores the latest Google search results
per page (app.services.search_console), re-scores every collection and applies
the lifecycle rules (app.services.collection_lifecycle: below the bar for
days -> noindex, no jobs for weeks -> archived), then finds new collections
worth a page (app.services.collection_discovery) and records them for
approval on the admin page. Never publishes or indexes anything — that's
a person's call.

Runs as an execution of the generate-static-job-pages Cloud Run job with
its args overridden (deploy/gcloud-deploy.sh §10): same image, database
and settings.

Dry run by default (logs what it would record, writes nothing); --apply
writes.

Usage:
    python collection_maintenance.py            # dry run
    python collection_maintenance.py --apply
"""

import argparse
import logging
from datetime import datetime, timezone

from app.db.session import SessionLocal
from app.services import collection_discovery, collection_lifecycle, search_console
from app.services.static_job_pages import load_link_index

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.collection_maintenance")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write. Without it, a dry run.")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        if args.apply:
            try:  # search data is report-only: its failure mustn't stop the rest
                search_console.ingest(db, now)
            except Exception:
                db.rollback()
                logger.exception("Search Console import failed; continuing without new search data.")
        live = [job for job in load_link_index(db, now).values() if not job.closed]
        changes = collection_lifecycle.reevaluate_all(db, live, now, apply=args.apply)
        found = collection_discovery.discover(db, live, now, apply=args.apply)
    for change in changes:
        logger.info("%s: %s -> %s (%s)", change.key, change.previous, change.new, change.reason)
    for proposal in found:
        e = proposal.evaluation
        logger.info("%s\t%d\t%s\t%d open jobs", proposal.key, e.total, e.recommendation.value, e.stats.open_jobs)
    logger.info(
        "Lifecycle: %d status change(s); discovery: %d new collection(s) for approval.%s",
        len(changes), len(found), "" if args.apply else " Dry run: nothing written.",
    )


if __name__ == "__main__":
    main()
