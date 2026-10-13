"""Standard titles on a Tech posting (JobPosting.standard_titles).

Set at scan time right after the sector (app.services.job_sector), from the
in-process model (app.services.standard_titles): 1-3 titles from the approved
list of standard tech titles. Only Tech postings (sector engineering_tech) are
tagged; anything else has none, so a rescan that moves a posting out of Tech
clears them.

Kept the same way as the sector: a rescan with the same title + description
keeps its titles without running the model (standard_title_input_hash), and
a Tech posting the model couldn't tag (standard_title_model NULL) is pending,
tagged by tag_pending_standard_titles — run hourly by retry_failed_scans.py.
That sweep also picks up postings whose sector was pending at scan time and
later turned out to be Tech.
"""

import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import JobSector
from app.models.job_posting import JobPosting
from app.services import standard_titles
from app.services.job_sector import input_hash

logger = logging.getLogger("app.job_standard_titles")


def store_titles(posting: JobPosting, titles: list[str] | None, text_hash: str) -> bool:
    """Write titles onto the posting (None = pending). Returns whether it was tagged."""
    if titles is None:
        posting.standard_titles = posting.standard_title_model = posting.standard_title_input_hash = None
        return False
    posting.standard_titles = titles
    posting.standard_title_model = standard_titles.model_version()
    posting.standard_title_input_hash = text_hash
    return True


def apply_standard_titles(posting: JobPosting) -> None:
    """Set a posting's standard titles during a scan, after its sector. Not
    Tech: none. Unchanged text: keeps the titles it has. Otherwise the model
    tags it, and a failure leaves it pending."""
    if posting.sector != JobSector.ENGINEERING_TECH:
        posting.standard_titles = posting.standard_title_model = posting.standard_title_input_hash = None
        return
    text_hash = input_hash(posting.title, posting.description)
    if posting.standard_title_model is not None and posting.standard_title_input_hash == text_hash:
        return
    store_titles(posting, standard_titles.tag_posting(posting.title, posting.description), text_hash)


def tag_pending_standard_titles(db: Session, *, time_budget_seconds: float = 150) -> tuple[int, int]:
    """Tag pending Tech postings until none are left or the time budget runs
    out. Stops at once if the model isn't available. Returns (tagged, still
    pending among those tried)."""
    deadline = time.monotonic() + time_budget_seconds
    tagged = failed = 0
    last_id = None
    while time.monotonic() < deadline:
        query = (
            select(JobPosting)
            .where(JobPosting.sector == JobSector.ENGINEERING_TECH, JobPosting.standard_title_model.is_(None))
            .order_by(JobPosting.id)
            .limit(50)
        )
        if last_id is not None:
            query = query.where(JobPosting.id > last_id)
        postings = db.scalars(query).all()
        if not postings:
            break
        last_id = postings[-1].id
        for posting in postings:
            titles = standard_titles.tag_posting(posting.title, posting.description)
            if store_titles(posting, titles, input_hash(posting.title, posting.description)):
                tagged += 1
            else:
                failed += 1
        db.commit()
        if failed and not tagged:
            logger.warning("Standard-title model unavailable; stopping the sweep until its next run.")
            break
    logger.info("Pending standard titles: %d tagged, %d still pending.", tagged, failed)
    return tagged, failed
