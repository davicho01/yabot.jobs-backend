"""Nightly upkeep of the job-collection registry (app.models.job_collection),
before the nightly page refresh: finds new collections worth a page
(app.services.collection_discovery) and records them for approval on the
admin page. Publishes nothing — the static generator renders only what a
person approved.

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
from app.services import collection_discovery
from app.services.static_job_pages import load_link_index

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.collection_maintenance")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write. Without it, a dry run.")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        live = [job for job in load_link_index(db, now).values() if not job.closed]
        found = collection_discovery.discover(db, live, now, apply=args.apply)
    for proposal in found:
        e = proposal.evaluation
        logger.info("%s\t%d\t%s\t%d open jobs", proposal.key, e.total, e.recommendation.value, e.stats.open_jobs)
    logger.info(
        "Discovery: %d new collection(s) %s.", len(found),
        "recorded for approval" if args.apply else "would be recorded (dry run: nothing written)",
    )


if __name__ == "__main__":
    main()
