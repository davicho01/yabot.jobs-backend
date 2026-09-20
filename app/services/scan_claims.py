"""Per-source politeness cap for page fetches.

A crawl can discover hundreds of job URLs for one board. Fetching them all
at once (any number of scan-function instances, each grabbing whatever
message it's handed) would hammer that one site, so scans of a crawl-sourced
URL are done by *lanes* (see app.services.jobs.run_source_lane): each lane
claims one URL at a time through claim_next_url, and claim_next_url refuses
once the source already has CrawlSource.max_concurrent_scans URLs in flight.

The cap is enforced here, in Postgres, not by how many Pub/Sub messages
exist — extra lanes for a source that's already at its cap simply claim
nothing and exit, so message duplication/redelivery can never push a source
over its limit.

"In flight" is just `scan_status = pending AND scan_claimed_at` is recent;
there's deliberately no extra scan_status value so the API's status enum
doesn't change. A claim older than the TTL belongs to a lane that died
mid-scan and is up for grabs again.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.crawl_source import CrawlSource
from app.models.enums import ScanStatus
from app.models.job_url import JobPostingUrl


@dataclass(frozen=True)
class ClaimedUrl:
    """Plain copy of what a lane needs, so it can fetch the page without an
    ORM row (and therefore without an open transaction/DB connection)."""

    id: uuid.UUID
    url: str


def _claim_cutoff(now: datetime) -> datetime:
    return now - timedelta(seconds=settings.scan_claim_ttl_seconds)


def claim_next_url(db: Session, source_id: uuid.UUID, now: datetime | None = None) -> ClaimedUrl | None:
    """Atomically claim the oldest unscanned URL of `source_id`, or return
    None if there's nothing left to claim or the source is already at its
    concurrency cap.

    The claim is committed before returning, which releases the row locks
    and the DB connection — the caller does the (slow) network fetch with no
    transaction open.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = _claim_cutoff(now)

    # Locking the source row serialises concurrent claims for *this* source
    # (other sources are unaffected), which is what makes the count-then-
    # claim below race-free. populate_existing so the cap is re-read rather
    # than served from a stale identity-map copy.
    source = db.scalar(
        select(CrawlSource).where(CrawlSource.id == source_id).with_for_update().execution_options(populate_existing=True)
    )
    if source is None:
        db.rollback()
        return None

    in_flight = db.scalar(
        select(func.count())
        .select_from(JobPostingUrl)
        .where(
            JobPostingUrl.crawl_source_id == source_id,
            JobPostingUrl.scan_status == ScanStatus.PENDING,
            JobPostingUrl.scan_claimed_at > cutoff,
        )
    )
    if in_flight >= source.max_concurrent_scans:
        db.rollback()
        return None

    url_row = db.scalar(
        select(JobPostingUrl)
        .where(
            JobPostingUrl.crawl_source_id == source_id,
            JobPostingUrl.scan_status == ScanStatus.PENDING,
            or_(JobPostingUrl.scan_claimed_at.is_(None), JobPostingUrl.scan_claimed_at <= cutoff),
        )
        .order_by(JobPostingUrl.created_at, JobPostingUrl.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if url_row is None:
        db.rollback()
        return None

    url_row.scan_claimed_at = now
    claimed = ClaimedUrl(id=url_row.id, url=url_row.url)
    db.commit()
    return claimed


def has_unclaimed_pending(db: Session, source_id: uuid.UUID, now: datetime | None = None) -> bool:
    """Whether `source_id` still has work no lane is currently on — i.e. a
    lane that's about to exit should leave a wake-up behind."""
    now = now or datetime.now(timezone.utc)
    cutoff = _claim_cutoff(now)
    found = db.scalar(
        select(JobPostingUrl.id)
        .where(
            JobPostingUrl.crawl_source_id == source_id,
            JobPostingUrl.scan_status == ScanStatus.PENDING,
            or_(JobPostingUrl.scan_claimed_at.is_(None), JobPostingUrl.scan_claimed_at <= cutoff),
        )
        .limit(1)
    )
    db.rollback()  # read-only; don't leave a transaction (connection) open
    return found is not None


def sources_with_pending_scans(db: Session) -> list[tuple[uuid.UUID, int]]:
    """(source_id, max_concurrent_scans) for every source that has at least
    one PENDING URL — the dispatcher's safety-net sweep publishes wake-ups
    for each (lost wake-ups, lanes that died, rows stranded by the
    pre-throttling per-URL queue)."""
    rows = db.execute(
        select(JobPostingUrl.crawl_source_id, CrawlSource.max_concurrent_scans)
        .join(CrawlSource, CrawlSource.id == JobPostingUrl.crawl_source_id)
        .where(JobPostingUrl.scan_status == ScanStatus.PENDING)
        .distinct()
    ).all()
    db.rollback()
    return [(source_id, lanes) for source_id, lanes in rows]
