"""One-off re-fan-out of the daily discovery crawl for a subset of sources.

Same mechanism as crawl_dispatcher.py — publishes one Pub/Sub message per
matching active CrawlSource, which crawl_worker.py then processes through
its normal discovery path — but scoped to specific ats_type(s) instead of
every active source, so it can be run once after a fix to re-pick-up
postings that were previously silently missed. Safe to run more than once:
get_or_create_job_posting no-ops on already-known URLs.

Usage:
    python -m one_off.recrawl_sources                       # default: ashby, greenhouse, gem
    python -m one_off.recrawl_sources --ats-type workday
    python -m one_off.recrawl_sources --ats-type ashby --ats-type icims
    python -m one_off.recrawl_sources --all-active           # every active source, any platform
"""

import argparse
import logging

from sqlalchemy import select

from app.core.log_config import configure_logging
from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.models.enums import CrawlSourceStatus
from app.services.crawl_queue import enqueue_crawl, ensure_topic_and_subscription

configure_logging()
logger = logging.getLogger("app.recrawl_sources")

DEFAULT_ATS_TYPES = ["ashby", "greenhouse", "gem"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--ats-type",
        action="append",
        dest="ats_types",
        help=f"Re-crawl only sources of this ats_type (repeatable). Default: {DEFAULT_ATS_TYPES}.",
    )
    parser.add_argument(
        "--all-active", action="store_true", help="Re-crawl every active source, ignoring --ats-type."
    )
    args = parser.parse_args()
    ats_types = None if args.all_active else (args.ats_types or DEFAULT_ATS_TYPES)

    ensure_topic_and_subscription()

    db = SessionLocal()
    try:
        stmt = select(CrawlSource).where(CrawlSource.status == CrawlSourceStatus.ACTIVE)
        if ats_types is not None:
            stmt = stmt.where(CrawlSource.ats_type.in_(ats_types))
        sources = db.scalars(stmt).all()

        logger.info(
            "Re-dispatching crawl for %d active source(s) (ats_types=%s).",
            len(sources),
            "all" if ats_types is None else ats_types,
        )
        for source in sources:
            try:
                enqueue_crawl(source.id)
                logger.info("Enqueued recrawl for %s (%s: %s)", source.name, source.ats_type, source.board_url)
            except Exception:
                logger.exception("Failed to enqueue recrawl for %s; skipping.", source.name)
    finally:
        db.close()


if __name__ == "__main__":
    main()
