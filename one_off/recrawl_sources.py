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
    python -m one_off.recrawl_sources --batch-size 4 --batch-delay 30
"""

import argparse
import logging
import time
from collections.abc import Iterable, Iterator, Sequence

from sqlalchemy import select

from app.core.log_config import configure_logging
from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.models.enums import CrawlSourceStatus
from app.services.crawl_queue import enqueue_crawl, ensure_topic_and_subscription

configure_logging()
logger = logging.getLogger("app.recrawl_sources")

DEFAULT_ATS_TYPES = ["ashby", "greenhouse", "gem"]
# crawl-worker's Cloud Run max-instances is 8 (concurrency 1 each, see
# deploy/gcloud-deploy.sh) — sized for the normal 2-hourly incremental
# dispatch, not a bulk re-fan-out. Firing every matching source at once blows
# through that cap and spills retry pressure onto shared downstream services:
# verified live 2026-09-22, a 1,051-source burst caused a sustained 429 storm
# on crawl-worker itself and on the shared yabot-jobs-browser rendering
# service real user submissions also depend on. Batching to roughly that cap,
# with a pause between batches, keeps a large re-fan-out within normal
# capacity instead of repeating that incident.
DEFAULT_BATCH_SIZE = 8
DEFAULT_BATCH_DELAY_SECONDS = 15


def _batched(items: Iterable[CrawlSource], size: int) -> Iterator[Sequence[CrawlSource]]:
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _recrawl_in_batches(sources: Sequence[CrawlSource], batch_size: int, batch_delay_seconds: float) -> None:
    batches = list(_batched(sources, batch_size))
    logger.info("Re-dispatching %d source(s) in %d batch(es) of up to %d.", len(sources), len(batches), batch_size)
    for batch_index, batch in enumerate(batches):
        for source in batch:
            try:
                enqueue_crawl(source.id)
                logger.info("Enqueued recrawl for %s (%s: %s)", source.name, source.ats_type, source.board_url)
            except Exception:
                logger.exception("Failed to enqueue recrawl for %s; skipping.", source.name)
        if batch_index < len(batches) - 1:
            logger.info(
                "Batch %d/%d done, pausing %ds before the next.", batch_index + 1, len(batches), batch_delay_seconds
            )
            time.sleep(batch_delay_seconds)


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
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"How many sources to enqueue before pausing (default: {DEFAULT_BATCH_SIZE}, matching crawl-worker's max-instances).",
    )
    parser.add_argument(
        "--batch-delay",
        type=float,
        default=DEFAULT_BATCH_DELAY_SECONDS,
        help=f"Seconds to pause between batches (default: {DEFAULT_BATCH_DELAY_SECONDS}).",
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
        logger.info("Matched %d active source(s) (ats_types=%s).", len(sources), "all" if ats_types is None else ats_types)
        _recrawl_in_batches(sources, args.batch_size, args.batch_delay)
    finally:
        db.close()


if __name__ == "__main__":
    main()
