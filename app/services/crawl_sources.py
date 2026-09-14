import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.crawl_source import CrawlSource
from app.models.enums import CrawlSourceStatus
from app.services.ats_adapters import detect_ats_source, detect_embedded_ats_source
from app.services.job_scanner import domain_of, normalize_url

logger = logging.getLogger("app.crawl_sources")


def register_discovered_board(db: Session, url: str) -> None:
    """Best-effort: note the board a submitted job URL belongs to, so the
    daily crawler picks up that company's future postings too. Never
    raises — a job submission must never fail because of this bookkeeping.

    A URL on a platform we already support becomes an "active" board,
    crawled starting with the next scheduled dispatch. A URL on a platform
    we don't recognize is instead noted as "pending" (keyed by domain, one
    row per unrecognized site regardless of how many people submit from
    it) — a queue of platforms worth investigating and implementing.
    """
    try:
        ats_type, board_token = detect_ats_source(url)
    except ValueError:
        embedded = detect_embedded_ats_source(url)
        if embedded is None:
            domain = domain_of(normalize_url(url))
            _upsert(
                db,
                name=domain,
                ats_type=None,
                board_token=None,
                detected_domain=domain,
                status=CrawlSourceStatus.PENDING,
            )
            return
        ats_type, board_token = embedded

    _upsert(
        db,
        name=f"{ats_type}/{board_token}",
        ats_type=ats_type,
        board_token=board_token,
        detected_domain=None,
        status=CrawlSourceStatus.ACTIVE,
    )


def _upsert(
    db: Session,
    *,
    name: str,
    ats_type: str | None,
    board_token: str | None,
    detected_domain: str | None,
    status: str,
) -> None:
    query = select(CrawlSource)
    query = (
        query.where(CrawlSource.ats_type == ats_type, CrawlSource.board_token == board_token)
        if ats_type is not None
        else query.where(CrawlSource.detected_domain == detected_domain)
    )
    if db.scalar(query) is not None:
        return

    try:
        with db.begin_nested():
            db.add(
                CrawlSource(
                    name=name,
                    ats_type=ats_type,
                    board_token=board_token,
                    detected_domain=detected_domain,
                    status=status,
                )
            )
    except IntegrityError:
        # Lost a race with a concurrent submission of the same board/domain
        # — the other insert already covers it.
        logger.info("Crawl source for %s already registered concurrently; skipping.", name)
