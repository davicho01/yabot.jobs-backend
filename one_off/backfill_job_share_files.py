"""One-off: write the link-preview file (jobs/_share/<url_id>, see
static_job_pages.render_job_share) for every job page already published.
The page generator writes one whenever it renders a page; this covers the
pages rendered before it did. Run it before the CloudFront function starts
serving these files to preview bots, or a bot gets the homepage's preview.

Safe to re-run: it writes the same file again. Only the share files: no job
page is re-rendered and the manifest is left alone. Needs SEO_PAGES_BUCKET
(or SEO_PAGES_OUTPUT_DIR locally).

A run stops after --max-seconds (default 1500, inside the Cloud Run job's
1800s timeout) and logs the --start for the next run, so the whole set goes
out over a few runs:

Usage:
    python -m one_off.backfill_job_share_files --dry-run
    python -m one_off.backfill_job_share_files
    python -m one_off.backfill_job_share_files --start 120000
"""

import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.services.static_job_pages import (
    CACHE_CONTROL,
    RENDER_BATCH_SIZE,
    UPLOAD_WORKERS,
    load_job_pages,
    parse_entry,
    read_job_manifest,
    render_job_share,
    share_key,
)
from app.services.static_pages import upload_html

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_job_share_files")


def backfill(db: Session, *, start: int = 0, max_seconds: float = 1500, dry_run: bool = False) -> int | None:
    """Writes share files for the manifest's pages from position `start`
    (sorted by url_id, so a later run picks up where this one stopped).
    Returns where the next run should start, or None once every page has one."""
    pages = read_job_manifest()["pages"]
    ids = sorted(pages)
    logger.info("%d published job page(s); starting at %d.", len(ids), start)
    began = time.monotonic()
    written = 0
    with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
        for at in range(start, len(ids), RENDER_BATCH_SIZE):
            if time.monotonic() - began > max_seconds:
                logger.info("Wrote %d; out of time. Next run: --start %d", written, at)
                return at
            batch = ids[at : at + RENDER_BATCH_SIZE]
            loaded = load_job_pages(db, batch)
            # A job gone from the database since its page went out keeps its
            # page until the generator retires it, which writes its share file.
            uploads = [(share_key(i), render_job_share(loaded[i], i, parse_entry(i, pages[i]).path)) for i in batch if i in loaded]
            if not dry_run:
                list(pool.map(lambda kv: upload_html(kv[0], kv[1], CACHE_CONTROL), uploads))
            written += len(uploads)
    logger.info("%s %d share file(s). Done.", "Would write" if dry_run else "Wrote", written)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=int, default=0, help="Position to start from (logged by the previous run).")
    parser.add_argument("--max-seconds", type=float, default=1500)
    parser.add_argument("--dry-run", action="store_true", help="Count only; write nothing.")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        backfill(db, start=args.start, max_seconds=args.max_seconds, dry_run=args.dry_run)
    finally:
        db.close()


if __name__ == "__main__":
    main()
