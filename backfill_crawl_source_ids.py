"""One-off backfill: populate JobPostingUrl.crawl_source_id for existing
rows that predate that column (it's only ever set going forward, at
creation time — see app.services.jobs.get_or_create_job_posting).

Matches each URL against the crawl_sources table by re-running the exact
same pattern-matching used everywhere else a board gets identified (see
app.services.ats_adapters.detect_ats_source) — no network calls, just
string matching, so this is safe and fast to run over the whole table.
Rows whose URL doesn't match any known ATS pattern, or matches a platform
we don't have an active CrawlSource for, are left alone (most likely a
directly user-submitted URL with no corresponding crawl, or a platform
still sitting in the "pending" queue).

Usage:
    python backfill_crawl_source_ids.py                # all matching rows
    python backfill_crawl_source_ids.py --limit 200 --dry-run
"""

import argparse
import logging

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.models.job_url import JobPostingUrl
from app.services.ats_adapters import detect_ats_source

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_crawl_source_ids")

_BATCH_SIZE = 500


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="Consider at most this many URL rows.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would be matched without writing anything."
    )
    return parser.parse_args()


def _build_source_index(db) -> dict[tuple[str, str], object]:
    """{(ats_type, board_token): source_id} for every active/known source —
    built once by re-deriving each source's own token from its board_url,
    so this stays correct even if a source's URL was hand-edited.
    """
    index: dict[tuple[str, str], object] = {}
    sources = db.scalars(select(CrawlSource).where(CrawlSource.ats_type.is_not(None))).all()
    for source in sources:
        try:
            ats_type, token = detect_ats_source(source.board_url)
        except ValueError:
            logger.warning("Source %s's board_url=%r doesn't match its own ats_type; skipping.", source.name, source.board_url)
            continue
        index[(ats_type, token)] = source.id
    return index


def main() -> None:
    args = _parse_args()

    db = SessionLocal()
    try:
        source_index = _build_source_index(db)
        logger.info("Loaded %d known source(s).", len(source_index))

        query = select(JobPostingUrl.id, JobPostingUrl.url).where(JobPostingUrl.crawl_source_id.is_(None))
        if args.limit:
            query = query.limit(args.limit)
        rows = db.execute(query).all()
        logger.info("Found %d URL(s) with no crawl_source_id.", len(rows))

        matched = no_pattern = no_source = 0
        pending_writes: list[tuple[object, object]] = []  # (url_id, source_id)

        for i, row in enumerate(rows, start=1):
            try:
                ats_type, token = detect_ats_source(row.url)
            except ValueError:
                no_pattern += 1
                continue

            source_id = source_index.get((ats_type, token))
            if source_id is None:
                no_source += 1
                continue

            matched += 1
            if args.dry_run:
                logger.info("[dry-run] (%d/%d) %s -> source_id=%s", i, len(rows), row.url, source_id)
                continue

            pending_writes.append((row.id, source_id))
            if len(pending_writes) >= _BATCH_SIZE:
                _flush(db, pending_writes)
                logger.info("(%d/%d) committed batch (%d matched so far)", i, len(rows), matched)

        if not args.dry_run:
            _flush(db, pending_writes)

        logger.info(
            "Done. %d matched%s, %d no ATS pattern, %d pattern matched but no such source.",
            matched,
            " (dry-run, nothing written)" if args.dry_run else "",
            no_pattern,
            no_source,
        )
    finally:
        db.close()


def _flush(db, pending_writes: list[tuple[object, object]]) -> None:
    if not pending_writes:
        return
    for url_id, source_id in pending_writes:
        db.execute(
            JobPostingUrl.__table__.update().where(JobPostingUrl.id == url_id).values(crawl_source_id=source_id)
        )
    db.commit()
    pending_writes.clear()


if __name__ == "__main__":
    main()
