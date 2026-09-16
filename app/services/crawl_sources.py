import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.crawl_source import CrawlSource
from app.models.enums import AtsType, CrawlSourceStatus
from app.services.ats_adapters import board_key_for, board_url_for_key, detect_ats_source, detect_embedded_ats_source
from app.services.job_scanner import domain_of, normalize_url

# ADP's board_key ("cid/ccId") is a pair of opaque client ids, not a
# company slug like every other adapter's — _company_name below can't tell
# that apart from a real slug by shape alone, so it's called out here
# explicitly rather than guessed at.
_NO_COMPANY_SLUG_ATS_TYPES = {AtsType.ADP}

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
        ats_type, board_key = detect_ats_source(url)
    except ValueError:
        embedded = detect_embedded_ats_source(url)
        if embedded is None:
            domain = domain_of(normalize_url(url))
            _upsert(
                db,
                name=domain,
                ats_type=None,
                board_key=None,
                board_url=f"https://{domain}",
                status=CrawlSourceStatus.PENDING,
            )
            return
        ats_type, board_key = embedded

    board_url = board_url_for_key(ats_type, board_key, url)
    _upsert(
        db,
        name=_company_name(ats_type, board_key, url),
        ats_type=ats_type,
        board_key=board_key,
        board_url=board_url,
        status=CrawlSourceStatus.ACTIVE,
    )


def _company_name(ats_type: str, board_key: str, url: str) -> str:
    """A human-readable label for the admin list — cosmetic only, not used
    for dedup or lookup (see board_url). board_key's first "/"-segment is
    the company slug for most platforms (e.g. Greenhouse's "anthropic",
    Workday's "salesforce/wd12/..."), so humanize that directly. A few
    platforms don't encode a company slug there at all — Oracle
    Fusion/Clinch/Eightfold's first segment is a hostname (contains a
    "."), and ADP's is a pair of opaque client ids (see
    _NO_COMPANY_SLUG_ATS_TYPES) — domain is the best fallback label
    available for those without an extra network fetch.
    """
    slug = board_key.split("/")[0]
    if not slug or "." in slug or ats_type in _NO_COMPANY_SLUG_ATS_TYPES:
        return domain_of(normalize_url(url))
    return slug.replace("-", " ").replace("_", " ").title()


def _upsert(
    db: Session, *, name: str, ats_type: str | None, board_key: str | None, board_url: str, status: str
) -> None:
    if _find_existing_board(db, ats_type, board_key, board_url) is not None:
        return

    try:
        with db.begin_nested():
            db.add(CrawlSource(name=name, ats_type=ats_type, board_url=board_url, status=status))
    except IntegrityError:
        # Lost a race with a concurrent submission of the same board —
        # the other insert already covers it.
        logger.info("Crawl source for %s already registered concurrently; skipping.", name)


def _find_existing_board(
    db: Session, ats_type: str | None, board_key: str | None, board_url: str
) -> CrawlSource | None:
    """A plain board_url text match is exact-and-sufficient for pending
    (ats_type-less) rows, and for any adapter whose board_url is a
    to_board_url-templated canonical value — both are always identical for
    repeat submissions of the same board. But adapters with no
    to_board_url (TalentBrew, Clinch, Oracle Fusion — see board_url_for_key)
    store board_url verbatim as whatever URL was actually submitted, so two
    submissions of different job URLs from the *same* board produce two
    different board_url strings. For those, re-derive each same-platform
    candidate's board_key from its own stored board_url (the same
    resolution list_job_urls uses) and compare that instead — otherwise
    every distinct job URL submitted from an already-registered board would
    register as its own duplicate "active" CrawlSource.
    """
    exact = db.scalar(select(CrawlSource).where(CrawlSource.board_url == board_url))
    if exact is not None or ats_type is None or board_key is None:
        return exact

    candidates = db.scalars(select(CrawlSource).where(CrawlSource.ats_type == ats_type))
    return next((c for c in candidates if board_key_for(ats_type, c.board_url) == board_key), None)
