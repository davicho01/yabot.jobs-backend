import html
import json
import logging
import time
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import false, func, or_, select, true
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.core.config import settings
from app.core.rate_limit import RateLimitExceeded
from app.models.enums import ApplicationStatus, EmploymentType, ScanStatus, WorkplaceType
from app.models.job_application import UserJobApplication
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services.crawl_sources import register_discovered_board
from app.services.job_dedup import find_duplicate_primary, normalize_company_name, normalize_title
from app.services.job_llm_extractor import LlmExtraction, extract_with_llm, html_to_text
from app.services import geo
from app.services.geo import resolve_area_codes, resolve_places
from app.services.job_locations import location_matches, radius_search, split_locations
from app.services.workplace import infer_workplace_type, reconcile_workplace_type
from app.services.job_queue import enqueue_scan, enqueue_source_scan
from app.services.job_scanner import ScanResult, domain_of, normalize_url, scan_job_url, url_hash
from app.services.llm_client import LlmError
from app.services.scan_claims import claim_next_url, has_unclaimed_pending, sources_with_pending_scans
from app.schemas.job import JobDetailRead, SearchAreaRead

logger = logging.getLogger("app.jobs")


def to_job_detail(url_row: JobPostingUrl) -> JobDetailRead:
    latest_posting = url_row.postings[0] if url_row.postings else None
    return JobDetailRead(url=url_row, posting=latest_posting)


def build_job_search_statement(
    *,
    q: str | None = None,
    location: str | None = None,
    metro: str | None = None,
    radius: int = geo.RADIUS_MILES,
    company: str | None = None,
    posted_within_days: int | None = None,
    workplace_type: str | None = None,
    salary_min: int | None = None,
    salary_max: int | None = None,
) -> tuple[Select, list, SearchAreaRead | None]:
    """The base GET /jobs query (canonical, scanned postings only) plus
    whichever of these filters are given, exactly as the route applies them
    — shared with the saved-search alert sweep (saved_search_alerts.py) so
    "does this posting match this search" is defined in exactly one place,
    not reimplemented and left to drift.

    Returns (stmt, order, search_area) — order (for stmt.order_by(*order))
    changes when a city search switches to nearest-first, and search_area is
    set the same way. Callers add anything route/script-specific on top:
    GET /jobs paginates and counts; the alert sweep instead adds a "posted or
    scanned since I last checked" bound and just wants matching rows.
    """
    stmt = (
        select(JobPostingUrl)
        .join(JobPosting, JobPosting.url_id == JobPostingUrl.id)
        .where(
            JobPosting.extraction_status == ScanStatus.SUCCESS,
            JobPosting.title.is_not(None),
            JobPosting.primary_posting_id.is_(None),
        )
    )
    order = [JobPostingUrl.created_at.desc()]
    search_area: SearchAreaRead | None = None
    # salary_min/salary_max use `is not None`, not truthy-`or` like the rest of
    # these — 0 is a valid, meaningful value for both and a plain `or` would
    # silently treat salary_min=0 as "not provided".
    if (
        q
        or location
        or metro
        or company
        or posted_within_days
        or workplace_type
        or salary_min is not None
        or salary_max is not None
    ):
        stmt = stmt.distinct()
        if q:
            stmt = stmt.where(JobPosting.title.ilike(f"%{q}%"))
        if location:
            metro_area = geo.search_metro(location)
            place = None if metro_area else geo.search_place(location)
            if metro_area is not None:
                # "Salt Lake City, Utah (metro area)": everything filed under that area.
                stmt = stmt.where(JobPosting.metros.contains([metro_area.code]))
            elif place is not None:
                # A city: postings with a place within `radius` miles of it, the
                # city's own first, then nearer before farther (see radius_search).
                nearest, near, band = radius_search(place, radius)
                stmt = stmt.join(nearest, true()).where(near).add_columns(band.label("distance_band"))
                order = [band, JobPostingUrl.created_at.desc()]
                search_area = SearchAreaRead(label=geo.place_label(place), radius_miles=radius)
            else:
                # A state or "United States" also finds postings filed under it
                # however they spelled the place; anything else is a plain text match.
                conditions = [location_matches(f"%{location}%")]
                conditions += [JobPosting.metros.contains([code]) for code in geo.search_areas(location) or []]
                stmt = stmt.where(or_(*conditions))
        if metro:
            metro_area = geo.metro_by_slug(metro)
            # An unknown slug matches nothing (rather than erroring): a stale
            # shared link should just show an empty board.
            stmt = stmt.where(JobPosting.metros.contains([metro_area.code]) if metro_area else false())
        if company:
            stmt = stmt.where(JobPosting.company_name.ilike(f"%{company}%"))
        if posted_within_days:
            stmt = stmt.where(JobPosting.posted_at >= date.today() - timedelta(days=posted_within_days))
        if workplace_type:
            stmt = stmt.where(JobPosting.workplace_type == workplace_type)
        # A posting often lists only one of salary_min/salary_max — compare
        # against whichever end of its range is actually set, falling back to
        # the other one, rather than requiring both. A posting with neither
        # set fails both comparisons (NULL >=/<= anything is NULL, which
        # WHERE treats as false), so an active salary filter also drops
        # postings with no pay listed at all — same as every other filter
        # here, which only ever narrows to postings that positively match.
        # Currencies aren't normalized: this compares raw numbers regardless
        # of salary_currency, acceptable while postings are overwhelmingly USD.
        if salary_min is not None:
            stmt = stmt.where(func.coalesce(JobPosting.salary_max, JobPosting.salary_min) >= salary_min)
        if salary_max is not None:
            stmt = stmt.where(func.coalesce(JobPosting.salary_min, JobPosting.salary_max) <= salary_max)

    return stmt, order, search_area


def get_or_create_job_posting(
    db: Session,
    raw_url: str,
    submitted_by_user_id: uuid.UUID | None,
    crawl_source_id: uuid.UUID | None = None,
    enqueue: bool = True,
    now: datetime | None = None,
) -> tuple[JobPosting, JobPostingUrl]:
    """Resolve a submitted URL to a (shared, app-wide) JobPosting.

    Dedupes on the normalized URL's hash. The JobPosting row itself always
    exists by the time this returns — for a brand-new URL it's created here
    as an empty PENDING shell and the real scan is queued (see
    process_scan_job, run by the worker) rather than run inline, since
    fetching a page plus an LLM call can take well over a minute, far too
    long to hold open inside a request. Callers distinguish "queued" from
    "actually scanned" via posting.extraction_status, not via None.

    enqueue=False leaves the new row PENDING without publishing a per-URL
    scan message. The crawl worker uses it: a crawl-sourced URL is scanned by
    that source's throttled lanes (see run_source_lane), which the crawler
    wakes once after it has recorded every URL, instead of publishing
    hundreds of independent messages at once.

    `now` is only for tests (see _enforce_submission_rate_limit) — real
    callers leave it unset and get the wall clock.
    """
    normalized = normalize_url(raw_url)
    hashed = url_hash(normalized)

    url_row = db.scalar(select(JobPostingUrl).where(JobPostingUrl.url_hash == hashed))
    if url_row is None:
        if submitted_by_user_id is not None:
            # Only a brand-new URL reaches here — re-submitting an already-known
            # one is a cheap dedup lookup, not a new scan, so it's never throttled.
            _enforce_submission_rate_limit(db, submitted_by_user_id, now=now)
        url_row = JobPostingUrl(
            url=raw_url,
            normalized_url=normalized,
            url_hash=hashed,
            domain=domain_of(normalized),
            submitted_by_user_id=submitted_by_user_id,
            crawl_source_id=crawl_source_id,
        )
        db.add(url_row)
        db.flush()
        posting = _create_pending_posting(db, url_row)
        # Committed before publishing, not just flushed: the worker reads
        # this row on a separate DB connection, and a flush is only visible
        # within this transaction. Publishing first risks the worker's
        # SELECT losing the race against this transaction's commit — it
        # would find no row, log "unknown url_id", and ack the message
        # anyway, stranding url_row at PENDING forever with nothing left to
        # redeliver it (verified happening locally against the Pub/Sub
        # emulator's low latency; see one_off/requeue_pending_scans.py for the
        # blunter recovery this replaces for the common case).
        db.commit()
        if enqueue:
            enqueue_scan(url_row.id)
        return posting, url_row

    posting = db.scalar(select(JobPosting).where(JobPosting.url_id == url_row.id))
    if posting is None:
        # Backfill for a URL row that predates eager posting creation.
        posting = _create_pending_posting(db, url_row)
        db.flush()
    return posting, url_row


def _enforce_submission_rate_limit(db: Session, user_id: uuid.UUID, now: datetime | None = None) -> None:
    """Throttle brand-new job-URL submissions per user — each one creates a
    JobPostingUrl row and queues a real LLM scan, so this caps runaway cost
    rather than request volume in general (see get_or_create_job_posting,
    the only caller).
    """
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=settings.job_submission_rate_limit_window_minutes)
    count = db.scalar(
        select(func.count())
        .select_from(JobPostingUrl)
        .where(JobPostingUrl.submitted_by_user_id == user_id, JobPostingUrl.created_at >= window_start)
    )
    if count >= settings.job_submission_rate_limit_max_new_urls:
        raise RateLimitExceeded("Too many new job submissions. Try again later.")


def _create_pending_posting(db: Session, url_row: JobPostingUrl) -> JobPosting:
    posting = JobPosting(url_id=url_row.id, extraction_status=ScanStatus.PENDING)
    db.add(posting)
    return posting


def find_existing_application(db: Session, user_id: uuid.UUID, job_posting_id: uuid.UUID) -> UserJobApplication | None:
    """`user_id`'s UserJobApplication for job_posting_id, or for any other
    JobPostingUrl found to be the same job (see app.services.job_dedup:
    JobPosting.primary_posting_id links a cross-posted duplicate to an
    existing "primary" row) — so saving/applying to a repost of something
    already tracked reuses that one row instead of a second, separately
    tracked application for what is, to the user, the same job. None if
    job_posting_id itself doesn't exist."""
    posting = db.get(JobPosting, job_posting_id)
    if posting is None:
        return None
    canonical_id = posting.primary_posting_id or posting.id
    return db.scalar(
        select(UserJobApplication)
        .join(JobPosting, UserJobApplication.job_posting_id == JobPosting.id)
        .where(
            UserJobApplication.user_id == user_id,
            or_(JobPosting.id == canonical_id, JobPosting.primary_posting_id == canonical_id),
        )
    )


def ensure_user_applicant(db: Session, user_id: uuid.UUID, job_posting_id: uuid.UUID) -> None:
    """Mark `user_id` as an applicant on `job_posting_id`, if not already linked
    (see find_existing_application — this also catches a cross-posted
    duplicate of a job they're already linked to under a different URL).

    Called wherever a user's own submission resolves to a JobPosting — either
    immediately (submit_job_url, when the URL was already scanned) or later
    (process_scan_job, once a brand-new submission's scan completes) — so
    submitting a job always adds it to the submitter's applications instead
    of requiring a separate POST /applications call.
    """
    existing = find_existing_application(db, user_id, job_posting_id)
    if existing is None:
        db.add(
            UserJobApplication(
                user_id=user_id,
                job_posting_id=job_posting_id,
                status=ApplicationStatus.APPLIED,
                applied_at=datetime.now(timezone.utc),
            )
        )
        # The session (SessionLocal, app/db/session.py) is autoflush=False, so
        # without this a second call in the same request/session — a
        # cross-posted duplicate resolving to a *different* job_posting_id —
        # wouldn't see this one's still-pending insert and would add another.
        db.flush()


def process_scan_job(db: Session, url_id: uuid.UUID) -> None:
    """Worker-side entry point: resolve board attribution, then fetch,
    extract, and store a JobPosting for the given JobPostingUrl. Guards
    against Pub/Sub's at-least-once delivery re-running (and re-billing) a
    scan that already completed.

    The url_row is fetched with SELECT ... FOR UPDATE so that guard is
    actually race-safe: a single worker process handles multiple messages
    concurrently (see worker.py's FlowControl), so two deliveries of the
    same message could otherwise both pass the "still pending" check before
    either commits, both scanning the same URL. The row lock forces the
    second delivery to wait for the first to commit its scan_status update,
    then see it's no longer PENDING and skip cleanly.
    """
    url_row = db.get(JobPostingUrl, url_id, with_for_update=True)
    if url_row is None:
        logger.warning("Scan job for unknown url_id=%s; skipping.", url_id)
        return

    if url_row.scan_status != ScanStatus.PENDING:
        logger.info("url_id=%s already scanned; skipping duplicate delivery.", url_id)
        return

    if url_row.crawl_source_id is None:
        # Only a genuinely user-submitted URL should grow the crawl
        # surface. A URL the crawler itself just discovered already
        # belongs to a known, active board (get_or_create_job_posting sets
        # crawl_source_id up front for those) — re-running board detection
        # on it is redundant at best, and actively wrong for adapters that
        # store board_url verbatim (TalentBrew, Clinch, Oracle Fusion):
        # each individual job URL would fail the existing-board_url check
        # and register as its own brand-new "active" CrawlSource, which
        # the dispatcher would then also crawl.
        #
        # Runs here rather than inline in the request (see
        # get_or_create_job_posting) because it can mean several
        # sequential page fetches — one per embedded-ATS adapter probing an
        # unrecognized platform — which this worker already budgets minutes
        # for per URL, unlike the request/response cycle that created this
        # row.
        source = register_discovered_board(db, url_row.url)
        if source is not None:
            url_row.crawl_source_id = source.id

    _apply_scan_result(db, url_row, scan_job_url(url_row.url))


def _backoff_seconds(attempts: int) -> float:
    """How long a FAILED row waits before it's next eligible for retry, given
    `attempts` consecutive failures (1 = the first failure). Grows
    scan_retry_backoff_multiplier-fold each time, capped at
    scan_retry_max_seconds — see the settings' own docstring in
    app.core.config for the resulting schedule."""
    return min(
        settings.scan_retry_base_seconds * settings.scan_retry_backoff_multiplier ** (attempts - 1),
        settings.scan_retry_max_seconds,
    )


def _apply_scan_result(db: Session, url_row: JobPostingUrl, result: ScanResult) -> None:
    now = datetime.now(timezone.utc)
    url_row.scan_error = _strip_nul(result.error)
    url_row.last_scanned_at = now
    url_row.scan_claimed_at = None

    if result.success:
        url_row.scan_status = ScanStatus.SUCCESS
        url_row.scan_attempts = 0
        url_row.next_retry_at = None
        _upsert_posting(db, url_row, result, now)
    else:
        url_row.scan_attempts += 1
        if url_row.scan_attempts >= settings.scan_retry_max_attempts:
            # Given up: a human has to rescan it (which resets scan_attempts)
            # for it to be eligible again — see rescan_job_url/rescan_crawl_source.
            url_row.scan_status = ScanStatus.NEEDS_REVIEW
            url_row.next_retry_at = None
        else:
            url_row.scan_status = ScanStatus.FAILED
            url_row.next_retry_at = now + timedelta(seconds=_backoff_seconds(url_row.scan_attempts))
        # A failed re-scan (transient WAF block, timeout, ...) must not
        # clobber a posting that already has good data from a previous
        # successful scan - same reasoning rescan_job_url already applies.
        # Only flip a posting that's never actually succeeded, so it's still
        # visibly not-pending rather than stuck PENDING forever with no
        # url_row left in PENDING to ever re-trigger it. Mirrors whichever of
        # FAILED/NEEDS_REVIEW the url_row above just got.
        posting = db.scalar(select(JobPosting).where(JobPosting.url_id == url_row.id))
        if posting is not None and posting.extraction_status != ScanStatus.SUCCESS:
            posting.extraction_status = url_row.scan_status
            posting.scanned_at = now
    db.flush()


def run_source_lane(db: Session, source_id: uuid.UUID) -> int:
    """Worker-side entry point for a "drain this source" wake-up: scan the
    source's pending URLs one at a time until none are left, this source is
    at its concurrency cap, or the lane's time budget is spent. Returns how
    many URLs this lane scanned.

    Several lanes may run for the same source at once (one per wake-up); the
    cap on how many actually fetch concurrently is enforced by
    claim_next_url, so a lane that finds the source already full just exits.
    Each URL is claimed (and the claim committed) *before* the fetch, and
    its result stored after — no transaction is open during the network
    call, so a slow site doesn't pin a DB connection or a row lock.

    A pause of scan_min_interval_seconds follows every fetch so a lane never
    sends a source back-to-back requests. When the time budget runs out with
    work left, the lane publishes a fresh wake-up and exits, letting other
    sources have the instance instead of one big board monopolising it.
    """
    deadline = time.monotonic() + settings.scan_lane_deadline_seconds
    scanned = 0
    logger.info("Lane started for source_id=%s.", source_id)

    while True:
        claimed = claim_next_url(db, source_id)
        if claimed is None:
            break

        fetch_started = time.monotonic()
        try:
            result = scan_job_url(claimed.url)
        except Exception as exc:
            # One page that blows up must not stall the rest of the source
            # (or, left PENDING, be reclaimed and blow up again after the
            # TTL forever). Record it like any other failed scan — it can be
            # retried from the admin rescan.
            logger.exception("Scan of %s (url_id=%s) raised; marking failed.", claimed.url, claimed.id)
            result = ScanResult(success=False, error=f"Scan raised {type(exc).__name__}: {exc}")

        try:
            url_row = db.get(JobPostingUrl, claimed.id, with_for_update=True)
            # Already finished by someone else (a stale-claim reclaim racing a
            # slow lane, say) — keep that result rather than overwrite it.
            if url_row is not None and url_row.scan_status == ScanStatus.PENDING:
                _apply_scan_result(db, url_row, result)
            db.commit()
        except (OperationalError, InterfaceError):
            # The database itself is unreachable/unhealthy — not this URL's
            # fault. Let the message be redelivered rather than blaming the URL.
            db.rollback()
            raise
        except Exception as exc:
            # Storing this page's result failed (data the columns can't hold,
            # say). Left alone the row would stay claimed, get reclaimed after
            # the TTL, and fail the same way forever, killing a lane each time.
            logger.exception("Storing the scan of %s (url_id=%s) failed; marking failed.", claimed.url, claimed.id)
            _mark_store_failed(db, claimed.id, exc)
        scanned += 1
        logger.info(
            "Scanned url_id=%s source_id=%s success=%s in %.1fs.",
            claimed.id,
            source_id,
            result.success,
            time.monotonic() - fetch_started,
        )

        if time.monotonic() >= deadline:
            if has_unclaimed_pending(db, source_id):
                enqueue_source_scan(source_id)
            break
        time.sleep(settings.scan_min_interval_seconds)

    logger.info("Lane for source_id=%s finished after scanning %d URL(s).", source_id, scanned)
    return scanned


def wake_sources_with_pending_scans(db: Session) -> int:
    """Safety-net sweep: publish wake-ups (one per allowed lane) for every
    source that still has PENDING URLs, so work whose original wake-up was
    lost, whose lane died mid-drain, or that was stranded by the old
    per-URL queue doesn't sit forever. Sources already at their cap just
    have the extra lanes exit immediately. Returns how many sources were
    woken; a failure for one source is logged and skipped.
    """
    woken = 0
    for source_id, lanes in sources_with_pending_scans(db):
        try:
            enqueue_source_scan(source_id, lanes=lanes)
            woken += 1
        except Exception:
            logger.exception("Failed to wake scan lanes for source_id=%s; skipping.", source_id)
    return woken


def wake_retryable_failed_scans(db: Session) -> int:
    """Retry sweep (see retry_failed_scans.py, its hourly-cron entrypoint):
    every FAILED row whose backoff window (next_retry_at) has elapsed is
    flipped back to PENDING and re-enqueued, same as a human clicking
    rescan, except automatic. NEEDS_REVIEW rows (out of retries) have no
    next_retry_at and are never selected here — only a deliberate rescan
    gets one moving again.

    Committed before publishing, not just flushed — same race avoided as
    get_or_create_job_posting: the worker reads these rows on a separate
    connection, so publishing before the commit risks it finding nothing.
    Returns how many rows were reset.
    """
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(JobPostingUrl).where(
            JobPostingUrl.scan_status == ScanStatus.FAILED,
            JobPostingUrl.next_retry_at.is_not(None),
            JobPostingUrl.next_retry_at <= now,
        )
    ).all()
    for url_row in rows:
        url_row.scan_status = ScanStatus.PENDING
        url_row.scan_claimed_at = None
        url_row.next_retry_at = None
    db.commit()

    for url_row in rows:
        # Crawl-sourced rows are worked through their source's throttled
        # lanes, not queued individually — the caller follows this with
        # wake_sources_with_pending_scans (same split one_off/requeue_pending_scans.py
        # already uses) to pick those up.
        if url_row.crawl_source_id is None:
            try:
                enqueue_scan(url_row.id)
            except Exception:
                logger.exception("Failed to re-queue url_id=%s; skipping.", url_row.id)
    return len(rows)


def _mark_store_failed(db: Session, url_id: uuid.UUID, exc: Exception) -> None:
    """Record that a page was fetched but its result couldn't be stored, in a
    fresh transaction (the failed one is rolled back first), releasing the
    claim so the URL doesn't hold one of its source's concurrency slots.

    Goes through the same attempt-count/backoff/give-up logic as any other
    scan failure (see _apply_scan_result) — a page whose data can't be stored
    (a bad shape the DB rejects, say) would otherwise retry forever at the
    sweep's cadence without ever reaching NEEDS_REVIEW.
    """
    db.rollback()
    now = datetime.now(timezone.utc)
    url_row = db.get(JobPostingUrl, url_id, with_for_update=True)
    if url_row is None or url_row.scan_status != ScanStatus.PENDING:
        db.rollback()
        return
    # First line only — a DB error's message embeds the whole failed statement.
    message = str(getattr(exc, "orig", None) or exc)
    detail = (message.splitlines() or [""])[0][:300]
    url_row.scan_error = _strip_nul(f"Storing the scan result failed ({type(exc).__name__}): {detail}")
    url_row.last_scanned_at = now
    url_row.scan_claimed_at = None
    url_row.scan_attempts += 1
    if url_row.scan_attempts >= settings.scan_retry_max_attempts:
        url_row.scan_status = ScanStatus.NEEDS_REVIEW
        url_row.next_retry_at = None
    else:
        url_row.scan_status = ScanStatus.FAILED
        url_row.next_retry_at = now + timedelta(seconds=_backoff_seconds(url_row.scan_attempts))
    posting = db.scalar(select(JobPosting).where(JobPosting.url_id == url_id))
    if posting is not None and posting.extraction_status != ScanStatus.SUCCESS:
        posting.extraction_status = url_row.scan_status
        posting.scanned_at = now
    db.commit()


def parse_scan_message(data: bytes) -> tuple[str, uuid.UUID]:
    """Decode a job-scan-requests payload into ("source", source_id) — a
    wake-up asking for a source's lane — or ("url", url_id), the original
    one-message-per-URL form (still published for user submissions, and
    possibly still sitting in the queue for crawl-sourced URLs from before
    per-source throttling). Raises ValueError if it's neither.
    """
    try:
        payload = json.loads(data.decode("utf-8"))
        if "source_id" in payload:
            return "source", uuid.UUID(payload["source_id"])
        return "url", uuid.UUID(payload["url_id"])
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"unrecognised scan message: {exc!r}") from exc


def process_scan_message(db: Session, kind: str, target_id: uuid.UUID) -> None:
    """Route a parsed scan message (see parse_scan_message).

    A per-URL message for a crawl-sourced URL is treated as a wake-up for
    that URL's source rather than scanned directly, so any such messages
    already queued when throttling shipped drain through the per-source cap
    too. Only URLs with no source (user submissions) are scanned on their
    own, unthrottled — that's low-volume, interactive traffic.
    """
    if kind == "url":
        source_id = db.scalar(select(JobPostingUrl.crawl_source_id).where(JobPostingUrl.id == target_id))
        db.rollback()  # read-only lookup; don't hold a connection open
        if source_id is None:
            process_scan_job(db, target_id)
            return
        target_id = source_id

    run_source_lane(db, target_id)


def rescan_job_url(db: Session, url_row: JobPostingUrl) -> JobPostingUrl:
    """User-triggered re-scan of a URL that's already been scanned at least
    once — e.g. to pick up an improved extractor, or recover from a one-off
    bad fetch (a page served mid-render, a transient block, etc).

    Unlike process_scan_job (the queue worker's entry point), this doesn't
    skip URLs that already have a posting — that guard exists only to stop
    Pub/Sub's at-least-once delivery from re-running the *same* scan
    request, not to block a deliberate rescan. A failed rescan attempt
    leaves the last-good JobPosting in place (just records the failure on
    the URL) rather than clobbering good data with a blank one — see
    _apply_scan_result, which this delegates to.

    A deliberate human rescan earns a fresh retry budget: reset scan_attempts
    to 0 first, so a URL that had exhausted its automatic retries (NEEDS_REVIEW)
    or was partway through backing off gets the full attempt count again
    rather than picking up where the automatic sweep left off.
    """
    url_row.scan_attempts = 0
    result = scan_job_url(url_row.url)
    _apply_scan_result(db, url_row, result)
    return url_row


# Must match the String(n) lengths on JobPosting (app/models/job_posting.py).
_TITLE_MAX = 255
_COMPANY_NAME_MAX = 255
_LOCATION_MAX = 255
_SALARY_CURRENCY_MAX = 3


def _strip_nul(value):
    """Remove NUL characters from a string, or from every string inside a
    (nested) dict/list — Postgres can't store \\u0000 in text or JSONB."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {_strip_nul(k): _strip_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_nul(v) for v in value]
    return value


def decode_entities(value: str | None) -> str | None:
    """HTML entities decoded ("Sales &amp; Marketing" -> "Sales & Marketing").
    Scraped titles, companies and locations often still carry them, which shows
    up literally on the job card — and the ";" inside one ("&amp;") reads as a
    separator between two places. Repeated for double-encoded text ("&amp;amp;")."""
    if value is None:
        return None
    for _ in range(3):
        decoded = html.unescape(value)
        if decoded == value:
            break
        value = decoded
    return value


def _fit(value: str | None, max_length: int) -> str | None:
    """NUL-stripped and truncated to a varchar(max_length) column."""
    if value is None:
        return None
    return _strip_nul(value)[:max_length]


def _upsert_posting(db: Session, url_row: JobPostingUrl, result: ScanResult, now: datetime) -> JobPosting:
    """Create or update the single JobPosting row for this URL (url_id is
    unique — one row per URL, updated in place on each scan/rescan, rather
    than an ever-growing history of one row per scan).
    """
    # LLM extraction disabled for now — heuristic-only extraction.
    # llm_extraction = _try_llm_extraction(result) if result.success else None
    llm_extraction = None
    fields = _merge_fields(result, llm_extraction)

    posting = db.scalar(select(JobPosting).where(JobPosting.url_id == url_row.id))
    if posting is None:
        posting = JobPosting(url_id=url_row.id)
        db.add(posting)
        # find_duplicate_primary (below) queries JobPosting by id — flush now
        # (this session doesn't autoflush) so a brand-new posting has one
        # before that query runs, instead of comparing against None.
        db.flush()

    # Scraped text is untrusted: a stray NUL byte (Postgres rejects it in
    # text *and* JSONB) or a title longer than its varchar column makes the
    # whole UPDATE fail — and since the failing message is retried, one such
    # page used to be re-scanned forever. Clean it on the way in instead.
    # Entities are decoded first, so what's stored (and shown) is real text.
    location = decode_entities(fields["location"])
    posting.title = _fit(decode_entities(fields["title"]), _TITLE_MAX)
    posting.company_name = _fit(decode_entities(fields["company_name"]), _COMPANY_NAME_MAX)
    posting.location = _fit(location, _LOCATION_MAX)
    # From the full string, not the 255-char display value above, so a long
    # list of locations isn't cut off partway for sources that don't
    # pre-truncate. (NUL-stripped for the same reason as everything else.)
    posting.locations = split_locations(_strip_nul(location) if location else None)
    posting.metros = resolve_area_codes(posting.locations)
    posting.places = resolve_places(posting.locations)
    # What the location text states ("Remote - US", "… HQ") beats an adapter's default
    # guess that a plain place means on-site — see app.services.workplace.
    posting.workplace_type = reconcile_workplace_type(fields["workplace_type"], infer_workplace_type(posting.locations))
    posting.employment_type = fields["employment_type"]
    posting.salary_min = fields["salary_min"]
    posting.salary_max = fields["salary_max"]
    posting.salary_currency = _fit(fields["salary_currency"], _SALARY_CURRENCY_MAX)
    posting.description = _strip_nul(result.description)
    posting.extracted_fields = _strip_nul(_merge_extracted_fields(result, llm_extraction))
    posting.raw_source = (
        {"html_excerpt": _strip_nul(result.raw_html_excerpt)} if result.raw_html_excerpt else None
    )
    posting.posted_at = fields["posted_at"]
    posting.scanned_at = now
    posting.extraction_status = ScanStatus.SUCCESS if result.success else ScanStatus.FAILED

    # Cross-source dedup (see app.services.job_dedup): recomputed on every
    # scan/rescan, so an adapter fix that changes a scraped title or company
    # reclassifies the grouping automatically. Only meaningful once there's
    # a title/company to match on, i.e. on a successful scan.
    if result.success:
        posting.company_key = normalize_company_name(posting.company_name)
        posting.title_key = normalize_title(posting.title)
        posting.primary_posting_id = find_duplicate_primary(db, posting)
    return posting


def _merge_fields(result: ScanResult, llm_extraction: LlmExtraction | None) -> dict:
    """LLM extraction wins when it has an actual answer; heuristic
    (JSON-LD/regex) extraction fills in anything the LLM left null/unknown.
    """
    if llm_extraction is None:
        return {
            "title": result.title,
            "company_name": result.company_name,
            "location": result.location,
            "workplace_type": result.workplace_type,
            "employment_type": result.employment_type,
            "salary_min": result.salary_min,
            "salary_max": result.salary_max,
            "salary_currency": result.salary_currency,
            "posted_at": result.posted_at,
        }
    return {
        "title": llm_extraction.title or result.title,
        "company_name": llm_extraction.company_name or result.company_name,
        "location": llm_extraction.location or result.location,
        "workplace_type": (
            llm_extraction.workplace_type
            if llm_extraction.workplace_type != WorkplaceType.UNKNOWN
            else result.workplace_type
        ),
        "employment_type": (
            llm_extraction.employment_type
            if llm_extraction.employment_type != EmploymentType.UNKNOWN
            else result.employment_type
        ),
        "salary_min": llm_extraction.salary_min if llm_extraction.salary_min is not None else result.salary_min,
        "salary_max": llm_extraction.salary_max if llm_extraction.salary_max is not None else result.salary_max,
        "salary_currency": llm_extraction.salary_currency or result.salary_currency,
        "posted_at": llm_extraction.posted_at or result.posted_at,
    }


def _try_llm_extraction(result: ScanResult) -> LlmExtraction | None:
    """Use the system-wide LLM (if configured) to extract job fields,
    falling back to heuristic-only extraction on any failure so a bad/missing
    key never breaks the scan.

    Note: this currently always uses the app-wide SYSTEM_LLM_* key, not a
    per-user one — the UserApiKey/is_default mechanism (app/models/api_key.py)
    is built and available for a future "bring your own key" feature but
    isn't consulted here yet.
    """
    if not result.full_html or not settings.system_llm_provider or not settings.system_llm_api_key:
        return None

    try:
        return extract_with_llm(
            html_to_text(result.full_html),
            provider=settings.system_llm_provider,
            model=settings.system_llm_model,
            api_key=settings.system_llm_api_key,
            base_url=settings.system_llm_base_url,
        )
    except LlmError as exc:
        logger.warning("LLM extraction failed, falling back to heuristic result: %s", exc)
        return None


def _merge_extracted_fields(
    result: ScanResult, llm_extraction: LlmExtraction | None
) -> dict | None:
    merged = dict(result.extracted_fields) if result.extracted_fields else {}
    if llm_extraction is not None:
        merged["_llm_extraction"] = llm_extraction.raw_response
    return merged or None
