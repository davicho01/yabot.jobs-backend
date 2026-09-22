"""Emails a digest for every saved search with new matches — see
saved_search_alerts.py at the repo root for the scheduled entrypoint that
calls sweep_saved_searches on a cadence.
"""

import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.models.saved_search import SavedSearch
from app.services import geo
from app.services.email import send_saved_search_digest_email
from app.services.jobs import build_job_search_statement, to_job_detail

logger = logging.getLogger("app.saved_search_alerts")

# How many matching postings to list in one digest email before summarizing
# the rest — a broad search's first sweep especially could match hundreds.
MAX_DIGEST_ITEMS = 10

# URL param names Header.tsx/JobBoardPage.tsx read — kept in sync by hand,
# there being no shared schema between the two repos to derive this from.
_BOARD_PARAM_NAMES = {
    "q": "q",
    "location": "location",
    "metro": "metro",
    "radius": "radius",
    "company": "company",
    "posted_within_days": "posted",
    "workplace_type": "workplace",
    "salary_min": "salaryMin",
    "salary_max": "salaryMax",
}


def _saved_search_board_url(saved_search: SavedSearch) -> str:
    """The board link a digest email points at to see the saved search
    itself (as opposed to any one matching posting)."""
    params = {
        url_name: str(value)
        for field, url_name in _BOARD_PARAM_NAMES.items()
        if (value := getattr(saved_search, field)) is not None and value != ""
    }
    query = urlencode(params)
    base = f"{settings.frontend_base_url}/jobs"
    return f"{base}?{query}" if query else base


def sweep_saved_searches(db: Session, now: datetime | None = None) -> int:
    """Sends a digest for every saved search with new matches since it was
    last checked (or since it was created, the first time a sweep sees it).
    Returns how many digests were sent.

    A search's last_alerted_at only advances when a digest is actually sent
    for it — a sweep that finds nothing new leaves it untouched, so the
    window it's checking against never grows past what's actually been
    covered, and a posting scanned in between two sweeps can't be missed.
    """
    now = now or datetime.now(timezone.utc)
    sent = 0
    saved_searches = db.scalars(select(SavedSearch).options(selectinload(SavedSearch.user))).all()
    logger.info("Sweeping %d saved search(es).", len(saved_searches))
    for saved_search in saved_searches:
        try:
            if _sweep_one(db, saved_search, now):
                sent += 1
        except Exception:
            logger.exception("Alert sweep failed for saved search %s; skipping.", saved_search.id)
    return sent


def _sweep_one(db: Session, saved_search: SavedSearch, now: datetime) -> bool:
    since = saved_search.last_alerted_at or saved_search.created_at
    stmt, order, _search_area = build_job_search_statement(
        q=saved_search.q,
        location=saved_search.location,
        metro=saved_search.metro,
        radius=saved_search.radius or geo.RADIUS_MILES,
        company=saved_search.company,
        posted_within_days=saved_search.posted_within_days,
        workplace_type=saved_search.workplace_type,
        salary_min=saved_search.salary_min,
        salary_max=saved_search.salary_max,
    )
    # scanned_at, not created_at: what actually makes a posting matchable
    # (title/company/salary/location) only exists once it's scanned, and a
    # crawl-discovered URL can sit PENDING for a while before that happens.
    # Caveat: a rescan bumps scanned_at on a posting already alerted about,
    # so it can resurface in a later digest — accepted for now rather than
    # adding a separate "already notified" table to prevent it.
    stmt = stmt.where(JobPosting.scanned_at >= since).order_by(*order)
    url_rows = db.scalars(stmt.options(selectinload(JobPostingUrl.postings))).all()
    if not url_rows:
        return False

    matches = [to_job_detail(row) for row in url_rows]
    send_saved_search_digest_email(
        saved_search.user.email,
        name=saved_search.name,
        matches=matches[:MAX_DIGEST_ITEMS],
        more_count=max(0, len(matches) - MAX_DIGEST_ITEMS),
        board_url=_saved_search_board_url(saved_search),
    )
    saved_search.last_alerted_at = now
    logger.info("Sent digest for saved search %s (%d match(es)).", saved_search.id, len(matches))
    return True
