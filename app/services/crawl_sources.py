import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.crawl_source import CrawlSource
from app.models.enums import AtsType, CrawlSourceStatus
from app.services.ats_adapters import canonical_board_url, detect_ats_source, detect_embedded_ats_source
from app.services.job_scanner import domain_of, normalize_url

# These two ats_types are white-label platforms with no shared, ATS-hosted
# canonical URL to reconstruct (each company's own domain *is* the board),
# so board_url is just the literal submitted URL, verbatim, rather than a
# templated one — see app.services.ats_adapters.list_job_urls, which
# already re-derives whatever it needs (host, siteNumber, ...) straight out
# of that URL rather than relying on a specific canonical shape.
_VERBATIM_BOARD_URL_ATS_TYPES = {AtsType.ORACLE_FUSION, AtsType.CLINCH}

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
            _upsert(db, name=domain, ats_type=None, board_url=f"https://{domain}", status=CrawlSourceStatus.PENDING)
            return
        ats_type, board_token = embedded

    board_url = url if ats_type in _VERBATIM_BOARD_URL_ATS_TYPES else canonical_board_url(ats_type, board_token)
    _upsert(
        db,
        name=f"{ats_type}/{board_token}",
        ats_type=ats_type,
        board_url=board_url,
        status=CrawlSourceStatus.ACTIVE,
    )


def _upsert(db: Session, *, name: str, ats_type: str | None, board_url: str, status: str) -> None:
    if db.scalar(select(CrawlSource).where(CrawlSource.board_url == board_url)) is not None:
        return

    try:
        with db.begin_nested():
            db.add(CrawlSource(name=name, ats_type=ats_type, board_url=board_url, status=status))
    except IntegrityError:
        # Lost a race with a concurrent submission of the same board —
        # the other insert already covers it.
        logger.info("Crawl source for %s already registered concurrently; skipping.", name)
