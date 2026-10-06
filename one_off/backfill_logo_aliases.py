"""One-off: give every company its stable logo alias (logos/c/<company>.png,
see app.services.company_logos.logo_alias_key) — a copy of its stored logo,
or the placeholder when it has none. Static job pages point at the alias, so
run this before the full re-render that switches them over to it.

From then on the alias is kept in step by everything that sets or clears a
logo (the scheduled sync, the admin endpoints), and the page generator writes
the placeholder for any new company it publishes a page for. Safe to re-run:
it writes the same bytes again. Needs SEO_PAGES_BUCKET (or
SEO_PAGES_OUTPUT_DIR locally).

Usage:
    python -m one_off.backfill_logo_aliases
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.company import Company
from app.services.company_logos import write_logo_alias
from app.services.page_store import get_page_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_logo_aliases")

WORKERS = 16


def main() -> None:
    store = get_page_store()
    db = SessionLocal()
    try:
        companies = db.execute(select(Company.company_key, Company.logo_key)).all()
    finally:
        db.close()

    def backfill(row) -> str:
        png = store.get(row.logo_key) if row.logo_key else None
        if row.logo_key and png is None:
            logger.warning("Stored logo %s for %r is missing; writing the placeholder.", row.logo_key, row.company_key)
        write_logo_alias(row.company_key, png, store=store)
        return "logo" if png else "placeholder"

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        outcomes = list(pool.map(backfill, companies))
    logger.info(
        "Wrote %d aliases: %d logos, %d placeholders.",
        len(outcomes), outcomes.count("logo"), outcomes.count("placeholder"),
    )


if __name__ == "__main__":
    main()
