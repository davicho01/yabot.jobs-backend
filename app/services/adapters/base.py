import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import httpx

TIMEOUT = 30.0

# How far back "recent" reaches for adapters that filter postings by date
# (Workday, Amazon, Apple, Oracle Fusion) rather than paginating a whole
# board every crawl. A single shared constant so every adapter's window
# moves together instead of drifting adapter-by-adapter. Includes today
# and the previous three days, allowing overlap between daily crawls
# while reducing work within the event-driven worker's timeout.
RECENT_WINDOW_DAYS = 7

# Shared result cap for every adapter.
# High-volume boards can hit this before exhausting their recent postings.
DEFAULT_MAX_JOBS_PER_CRAWL = 500


def posting_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).date() if parsed.tzinfo else parsed.date()
    except ValueError:
        return None


def is_recent_posting(value: object) -> bool:
    posted = posting_date(value)
    if posted is None:
        return False
    today = datetime.now(timezone.utc).date()
    return today - timedelta(days=RECENT_WINDOW_DAYS - 1) <= posted <= today


def limit_job_urls(urls: Iterable[str]) -> list[str]:
    result = []
    seen = set()
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            result.append(url)
            if len(result) >= DEFAULT_MAX_JOBS_PER_CRAWL:
                break
    return result

logger = logging.getLogger(__name__)

# job_scanner.py's own retry helper caps at 3 attempts / 18s max backoff,
# tuned for a single interactive job-page fetch. That budget isn't enough
# here: verified live against Microsoft's Eightfold tenant, whose rate
# limiter keeps tripping every few pages during a ~25-request pagination
# run and needed 6 attempts / a 30s ceiling to ride out (crawl_worker.py has
# no tight time budget, so the extra wall-clock cost here is fine).
_RATE_LIMIT_MAX_ATTEMPTS = 6
_RATE_LIMIT_MAX_WAIT_SECONDS = 60.0
_RATE_LIMIT_BACKOFF_SECONDS = (2.0, 5.0, 10.0, 20.0, 30.0)


def _rate_limit_wait_seconds(response: httpx.Response, attempt: int) -> float:
    """How long to wait before retrying a 429, preferring the site's own
    Retry-After over a guessed backoff — capped so a site advertising an
    hours-long Retry-After can't pin a worker slot on one URL indefinitely.
    Mirrors job_scanner.py's helper of the same name/shape.
    """
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            wait = float(retry_after)
        except ValueError:
            wait = None  # HTTP-date form — not worth parsing, fall back to backoff
        if wait is not None and wait >= 0:
            return min(wait, _RATE_LIMIT_MAX_WAIT_SECONDS)
    return _RATE_LIMIT_BACKOFF_SECONDS[attempt]


def _request_with_retry(request_fn: Callable[[], httpx.Response], url: str) -> httpx.Response:
    """Shared retry loop behind get_with_retry/post_with_retry: retries a
    few times on 429, for tenant APIs hit repeatedly in a tight pagination
    loop (e.g. Eightfold's /api/pcsx/search) — a burst of back-to-back
    requests to the same host can trip the tenant's own rate limiter partway
    through. Does not call raise_for_status(); callers do that themselves,
    same as a plain httpx.get()/httpx.post() call.
    """
    attempt = 0
    while True:
        response = request_fn()
        attempt += 1
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS and attempt < _RATE_LIMIT_MAX_ATTEMPTS:
            wait = _rate_limit_wait_seconds(response, attempt - 1)
            logger.info("Rate limited fetching %s (attempt %d); retrying in %.1fs.", url, attempt, wait)
            time.sleep(wait)
            continue
        return response


def get_with_retry(url: str, *, timeout: float = TIMEOUT, **kwargs) -> httpx.Response:
    return _request_with_retry(lambda: httpx.get(url, timeout=timeout, **kwargs), url)


def post_with_retry(url: str, *, timeout: float = TIMEOUT, **kwargs) -> httpx.Response:
    return _request_with_retry(lambda: httpx.post(url, timeout=timeout, **kwargs), url)


@dataclass(frozen=True)
class AtsAdapter:
    """Everything needed to support one ATS platform, in one place.

    board_key is an internal identifier private to a given adapter — for
    most platforms it's just the board slug/subdomain, but some need more
    than one value (e.g. Workday's "company/instance/site", ADP's
    "cid/ccId") and just join them with "/"; nothing outside this module
    ever needs to know or care about that shape, since match/board_key/
    fetch_jobs/to_board_url are always the ones both producing and
    consuming it.

    match: pure string URL matching, no I/O — None for platforms with no
        static URL shape (Eightfold, Clinch), which only match via
        embedded_match.
    board_key: re-derives board_key from a *stored* board_url for
        listing, when that's cheaper/different than match (Eightfold,
        Clinch only — a trivial host extraction, since their real
        board_key needs guess-and-verify network calls that fetch_jobs
        already does anyway). Defaults to match when unset.
    to_board_url: board_key -> the canonical URL to persist as
        CrawlSource.board_url. None means "verbatim": store whatever URL
        was actually submitted, unchanged (see board_url_for_key in
        app.services.ats_adapters).
    embedded_match: url -> board_key, a slower I/O-based fallback tier for
        platforms white-labeled onto a company's own domain, where the
        board_key isn't visible in the URL string alone.
    """

    ats_type: str
    fetch_jobs: Callable[[str], list[str]]
    match: Callable[[str], str | None] | None = None
    board_key: Callable[[str], str | None] | None = None
    to_board_url: Callable[[str], str] | None = None
    embedded_match: Callable[[str], str | None] | None = None


# Shared by every embedded-widget detector that has to guess a board slug
# from a company's own domain (Greenhouse, Ashby) — e.g. "www.anrok.com" ->
# ["anrok"]. Also strips a trailing corporate suffix as a second guess
# (verified against a real board: "coalitioninc.com" -> "coalition") —
# wrong guesses just fail the caller's verification step harmlessly, same
# as any other candidate here.
_COMPANY_SUFFIXES = ("incorporated", "corp", "inc", "llc", "ltd", "group", "co")


def candidate_slugs_from_domain(domain: str) -> list[str]:
    label = domain.lower().removeprefix("www.").split(".")[0]
    candidates = [label]
    for suffix in _COMPANY_SUFFIXES:
        if label.endswith(suffix) and len(label) > len(suffix):
            candidates.append(label[: -len(suffix)])
    return list(dict.fromkeys(candidates))  # dedupe, keep order


def parse_month_day_year(raw: str | None, *, month_style: str) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(" ".join(raw.split()), month_style).date()
    except ValueError:
        return None
