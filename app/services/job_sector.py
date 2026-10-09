"""Job function/department (sector) inferred from a posting's own text.

Unlike workplace_type (read off a posting's location entries, see
app.services.workplace) this can't be derived from anything company-level:
the same company can post HR, Finance, and Engineering roles at once, so
each posting has to be classified from what it itself says it's hiring for.

Classified by the sector model service (app.services.sector_api), trained in
yabot.jobs-ml on ~8,500 real postings labeled by job function (full title +
description). There is no fallback model: when the service doesn't answer,
the posting is saved as pending (sector_model NULL, sector unknown) and
classify_pending_sectors — run hourly by retry_failed_scans.py — classifies it.

A rescan with the same title + description keeps its sector without calling
the service (sector_input_hash). Below settings.sector_confidence_threshold
the sector is unknown: a wrong sector page hurts more than a missing one.
"""

import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import JobSector
from app.models.job_posting import JobPosting
from app.services.sector_api import SectorPrediction, classify_remote

logger = logging.getLogger("app.job_sector")

_VALID_SECTORS = {s.value for s in JobSector}


def input_hash(title: str | None, description: str | None) -> str:
    """Short fingerprint of the text a sector is computed from."""
    return hashlib.sha256(f"{title or ''}\x00{description or ''}".encode()).hexdigest()[:16]


def sector_for(prediction: SectorPrediction) -> JobSector:
    """The sector to show for a prediction: unknown below the threshold."""
    if prediction.confidence < settings.sector_confidence_threshold or prediction.sector not in _VALID_SECTORS:
        return JobSector.UNKNOWN
    return JobSector(prediction.sector)


def store_prediction(posting: JobPosting, prediction: SectorPrediction | None, text_hash: str) -> bool:
    """Write a prediction onto the posting (None = pending). Returns whether it was classified."""
    if prediction is None:  # pending: the sweep retries it
        posting.sector = JobSector.UNKNOWN
        posting.sector_model = posting.sector_confidence = posting.sector_input_hash = None
        return False
    posting.sector = sector_for(prediction)
    posting.sector_model = prediction.model
    posting.sector_confidence = prediction.confidence
    posting.sector_input_hash = text_hash
    return True


def apply_sector(posting: JobPosting, title: str | None, description: str | None) -> None:
    """Set the posting's sector during a scan. Unchanged text keeps the sector
    it already has; otherwise the service is called, and a failed call leaves
    the posting pending."""
    text_hash = input_hash(title, description)
    if posting.sector_model is not None and posting.sector_input_hash == text_hash:
        return
    store_prediction(posting, classify_remote(title, description), text_hash)


def classify_pending_sectors(db: Session, *, time_budget_seconds: float = 300, workers: int = 4) -> tuple[int, int]:
    """Classify postings left pending by a failed API call, until none are left
    or the time budget runs out. Stops early if the service is down (a whole
    batch fails). Returns (classified, still pending among those tried)."""
    deadline = time.monotonic() + time_budget_seconds
    classified = failed = 0
    last_id = None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while time.monotonic() < deadline:
            query = select(JobPosting).where(JobPosting.sector_model.is_(None)).order_by(JobPosting.id).limit(50)
            if last_id is not None:
                query = query.where(JobPosting.id > last_id)
            postings = db.scalars(query).all()
            if not postings:
                break
            last_id = postings[-1].id
            predictions = list(pool.map(lambda p: classify_remote(p.title, p.description), postings))
            for posting, prediction in zip(postings, predictions):
                if store_prediction(posting, prediction, input_hash(posting.title, posting.description)):
                    classified += 1
                else:
                    failed += 1
            db.commit()
            if all(p is None for p in predictions):
                logger.warning("Sector API failed for a whole batch; stopping the sweep until its next run.")
                break
    logger.info("Pending sectors: %d classified, %d still pending.", classified, failed)
    return classified, failed
