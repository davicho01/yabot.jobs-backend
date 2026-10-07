"""One-off cleanup: postings whose "successful" scan was really a bot-
management interstitial (Cloudflare "Just a moment...", Akamai "Access
Denied", ...) — 65 live listings as of 2026-10-07, before fetch_html /
fetch_rendered_page learned to reject those pages (see
app.services.browser_fetch.is_bot_challenge_page).

A failed rescan deliberately never clobbers a posting that once scanned
successfully, so these don't heal on their own. This marks each posting
FAILED (out of search and the /job page manifest, both of which require
SUCCESS) and its URL FAILED with a retry due now, so wake_retryable_failed_
scans rescans it; the first scan that gets the real page restores it.

Usage:
    python -m one_off.clear_bot_challenge_postings --dry-run
    python -m one_off.clear_bot_challenge_postings
"""

import argparse
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.enums import ScanStatus
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services.browser_fetch import _BOT_CHALLENGE_TITLES, is_bot_challenge_title

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.clear_bot_challenge_postings")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="List matching postings without changing them.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Narrow in SQL on the lowercased title, then apply the exact same
        # normalization the fetch path uses.
        candidates = db.execute(
            select(JobPosting, JobPostingUrl)
            .join(JobPostingUrl, JobPostingUrl.id == JobPosting.url_id)
            .where(func.lower(func.trim(JobPosting.title)).in_(_BOT_CHALLENGE_TITLES))
        ).all()
        matches = [(posting, url_row) for posting, url_row in candidates if is_bot_challenge_title(posting.title)]
        logger.info("Found %d posting(s) titled like a bot-challenge page.", len(matches))
        for posting, url_row in matches:
            logger.info(
                "%s %r | %s | %s | posting=%s url=%s",
                "[dry-run]" if args.dry_run else "clearing",
                posting.title,
                posting.company_name,
                url_row.url,
                posting.extraction_status,
                url_row.scan_status,
            )
        if args.dry_run:
            return

        now = datetime.now(timezone.utc)
        for posting, url_row in matches:
            posting.extraction_status = ScanStatus.FAILED
            url_row.scan_status = ScanStatus.FAILED
            url_row.scan_attempts = 0
            url_row.next_retry_at = now
        db.commit()
        logger.info("Cleared %d posting(s); their URLs are due for a rescan.", len(matches))
    finally:
        db.close()


if __name__ == "__main__":
    main()
