from app.services.adapters import ADAPTERS
from app.services.adapters.base import AtsAdapter, limit_job_urls

_ADAPTERS_BY_TYPE: dict[str, AtsAdapter] = {adapter.ats_type: adapter for adapter in ADAPTERS}
_EMBEDDED_MATCH_ADAPTERS = [adapter for adapter in ADAPTERS if adapter.embedded_match is not None]


def detect_ats_source(url: str) -> tuple[str, str]:
    """Given any careers/job URL, return (ats_type, board_key) by matching
    it against each platform's known URL shape (see app.services.adapters).
    Raises ValueError if the URL doesn't match a supported platform —
    callers should fall back to detect_embedded_ats_source, and treat a
    None from that as "not a platform we support yet."
    """
    for adapter in ADAPTERS:
        if adapter.match is None:
            continue
        key = adapter.match(url)
        if key is not None:
            return adapter.ats_type, key
    raise ValueError(f"Couldn't detect a supported ATS from url={url!r}.")


def board_key_for(ats_type: str, board_url: str) -> str | None:
    """Re-derive board_key from a *stored* board_url — the same
    board_key-or-match resolution list_job_urls uses. None means either an
    unsupported ats_type or a board_url that doesn't actually look like one
    of that platform's boards.
    """
    adapter = _ADAPTERS_BY_TYPE.get(ats_type)
    if adapter is None:
        return None
    key_fn = adapter.board_key or adapter.match
    return key_fn(board_url) if key_fn else None


def list_job_urls(ats_type: str, board_url: str) -> list[str]:
    """Discovery only: return every current job-posting URL for a company's
    board. Field extraction (title, salary, etc.) is left entirely to the
    existing scan pipeline (app.services.job_scanner) once each URL is
    submitted via get_or_create_job_posting — this just finds the URLs.

    Takes the board's stored URL rather than a pre-extracted key — every
    platform's adapter (see app.services.adapters) knows how to re-derive
    whatever it needs straight out of that URL.
    """
    adapter = _ADAPTERS_BY_TYPE.get(ats_type)
    if adapter is None:
        raise ValueError(f"Unsupported ats_type: {ats_type!r}")
    key = board_key_for(ats_type, board_url)
    if key is None:
        raise ValueError(f"board_url={board_url!r} doesn't look like a {ats_type} board")
    return limit_job_urls(adapter.fetch_jobs(key))


def board_url_for_key(ats_type: str, board_key: str, submitted_url: str) -> str:
    """The value to persist as CrawlSource.board_url for a newly detected
    (ats_type, board_key) — the inverse of detect_ats_source for platforms
    whose adapter defines to_board_url. Used by register_discovered_board
    (both going forward, and for the one-time migration that backfilled
    existing rows) so it always round-trips back through
    detect_ats_source / list_job_urls.

    Platforms without a to_board_url (Oracle Fusion, Clinch) store
    submitted_url verbatim instead — see AtsAdapter's docstring for why.
    """
    adapter = _ADAPTERS_BY_TYPE[ats_type]
    return submitted_url if adapter.to_board_url is None else adapter.to_board_url(board_key)


def detect_embedded_ats_source(url: str) -> tuple[str, str] | None:
    """Best-effort fallback for when detect_ats_source's pure string match
    fails: some companies white-label an ATS's embeddable widget onto their
    own domain instead of linking out to the ATS's own hosted board, so the
    board key isn't visible in the URL string alone — recovering it takes
    real network I/O (fetching the page, sometimes probing a guessed key
    against the ATS's own API), unlike every pure-string check above.
    Never raises — this is a fallback for a ValueError, so a fetch failure
    here must not break the job submission it's trying to enrich; caller
    treats None as "still couldn't detect."
    """
    for adapter in _EMBEDDED_MATCH_ADAPTERS:
        key = adapter.embedded_match(url)
        if key is not None:
            return adapter.ats_type, key
    return None
