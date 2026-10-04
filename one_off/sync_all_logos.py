"""One-off catch-up: run the company logo sync (see
app.services.company_logos.sync_company_logos) round after round until
every company due a logo has been tried once — instead of the scheduled
static-pages runs' 300 per run, which takes weeks through a large backlog.

Same behavior as a scheduled run otherwise: logos are fetched from
logo.dev with the secret key, stored as our own copies in the static-pages
bucket, and admin-set logos are never touched. Companies logo.dev is still
indexing are left for the scheduled runs. Safe to re-run or stop at any
time. Needs LOGO_DEV_SECRET_KEY and SEO_PAGES_BUCKET (or
SEO_PAGES_OUTPUT_DIR locally).

Pages pick the new logos up on the next scheduled static-pages run (each
job page's version includes its logo).

Usage:
    python -m one_off.sync_all_logos [--max-minutes 50]
"""

import argparse
import logging
import time

from app.db.session import SessionLocal
from app.services import logo_dev
from app.services.company_logos import sync_company_logos

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("app.sync_all_logos")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-minutes", type=float, default=50, help="Stop starting new rounds after this long.")
    args = parser.parse_args()
    if not logo_dev.is_configured():
        raise SystemExit("LOGO_DEV_SECRET_KEY is not set.")

    deadline = time.monotonic() + args.max_minutes * 60
    attempted: set[str] = set()
    totals: dict[str, int] = {}
    db = SessionLocal()
    try:
        while time.monotonic() < deadline:
            budget = min(300.0, deadline - time.monotonic())
            counts = sync_company_logos(db, budget_seconds=budget, attempted=attempted)
            if not counts:
                break  # nothing left that this run hasn't tried
            for status, n in counts.items():
                totals[status] = totals.get(status, 0) + n
            logger.info("Round done: %s — running totals: %s (%d companies tried)", counts, totals, len(attempted))
        logger.info("Done. %s, %d companies tried.", totals, len(attempted))
    finally:
        db.close()


if __name__ == "__main__":
    main()
