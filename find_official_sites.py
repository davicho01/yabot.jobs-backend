"""Daily look for the official careers sites of companies whose jobs we only
found somewhere else — see app.services.official_sites for the logic.

Cloud Scheduler calls dispatch() (a Cloud Function, like
follow_up_reminders.py). Run it by hand, e.g. on the one-off Cloud Run job,
to see what it would add without writing anything:

Usage:
    python find_official_sites.py --dry-run            # report only
    python find_official_sites.py --dry-run --limit 10 # just the top 10
    python find_official_sites.py                      # register the boards found

Each run checks companies until its time budget (official_sites.
TIME_BUDGET_SECONDS) is used; the next run continues with the rest.
"""

import argparse
import logging

from app.core.log_config import configure_logging
from app.db.session import SessionLocal
from app.services.official_sites import run

configure_logging()
logger = logging.getLogger("app.find_official_sites")


def main(*, dry_run: bool = False, limit: int | None = None) -> None:
    db = SessionLocal()
    try:
        run(db, dry_run=dry_run, limit=limit)
    finally:
        db.close()


def dispatch(_request=None) -> tuple[str, int]:
    """Cloud Functions (2nd gen) HTTP entry point — Cloud Scheduler calls this
    daily instead of a shell invoking main() directly."""
    main()
    return "ok", 200


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Report the boards it would add; write nothing.")
    parser.add_argument("--limit", type=int, help="At most this many companies (default: as many as the time budget allows).")
    args = parser.parse_args()
    main(dry_run=args.dry_run, limit=args.limit)
