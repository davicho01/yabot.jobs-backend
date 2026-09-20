import json
import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import ApplicationStatus, EmploymentType, ScanStatus, WorkplaceType
from app.models.job_application import UserJobApplication
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services.crawl_sources import register_discovered_board
from app.services.job_llm_extractor import LlmExtraction, extract_with_llm, html_to_text
from app.services.job_queue import enqueue_scan, enqueue_source_scan
from app.services.job_scanner import ScanResult, domain_of, normalize_url, scan_job_url, url_hash
from app.services.llm_client import LlmError
from app.services.scan_claims import claim_next_url, has_unclaimed_pending, sources_with_pending_scans
from app.schemas.job import JobDetailRead

logger = logging.getLogger("app.jobs")


def to_job_detail(url_row: JobPostingUrl) -> JobDetailRead:
    latest_posting = url_row.postings[0] if url_row.postings else None
    return JobDetailRead(url=url_row, posting=latest_posting)


def get_or_create_job_posting(
    db: Session,
    raw_url: str,
    submitted_by_user_id: uuid.UUID | None,
    crawl_source_id: uuid.UUID | None = None,
    enqueue: bool = True,
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
    """
    normalized = normalize_url(raw_url)
    hashed = url_hash(normalized)

    url_row = db.scalar(select(JobPostingUrl).where(JobPostingUrl.url_hash == hashed))
    if url_row is None:
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
        # emulator's low latency; see requeue_pending_scans.py for the
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


def _create_pending_posting(db: Session, url_row: JobPostingUrl) -> JobPosting:
    posting = JobPosting(url_id=url_row.id, extraction_status=ScanStatus.PENDING)
    db.add(posting)
    return posting


def ensure_user_applicant(db: Session, user_id: uuid.UUID, job_posting_id: uuid.UUID) -> None:
    """Mark `user_id` as an applicant on `job_posting_id`, if not already linked.

    Called wherever a user's own submission resolves to a JobPosting — either
    immediately (submit_job_url, when the URL was already scanned) or later
    (process_scan_job, once a brand-new submission's scan completes) — so
    submitting a job always adds it to the submitter's applications instead
    of requiring a separate POST /applications call.
    """
    existing = db.scalar(
        select(UserJobApplication).where(
            UserJobApplication.user_id == user_id,
            UserJobApplication.job_posting_id == job_posting_id,
        )
    )
    if existing is None:
        db.add(
            UserJobApplication(
                user_id=user_id,
                job_posting_id=job_posting_id,
                status=ApplicationStatus.APPLIED,
                applied_at=datetime.now(timezone.utc),
            )
        )


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


def _apply_scan_result(db: Session, url_row: JobPostingUrl, result: ScanResult) -> None:
    now = datetime.now(timezone.utc)
    url_row.scan_status = ScanStatus.SUCCESS if result.success else ScanStatus.FAILED
    url_row.scan_error = _strip_nul(result.error)
    url_row.last_scanned_at = now
    url_row.scan_claimed_at = None
    if result.success:
        _upsert_posting(db, url_row, result, now)
    else:
        # A failed re-scan (transient WAF block, timeout, ...) must not
        # clobber a posting that already has good data from a previous
        # successful scan - same reasoning rescan_job_url already applies.
        # Only flip a posting that's never actually succeeded to FAILED, so
        # it's still visibly not-pending rather than stuck PENDING forever
        # with no url_row left in PENDING to ever re-trigger it.
        posting = db.scalar(select(JobPosting).where(JobPosting.url_id == url_row.id))
        if posting is not None and posting.extraction_status != ScanStatus.SUCCESS:
            posting.extraction_status = ScanStatus.FAILED
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


def _mark_store_failed(db: Session, url_id: uuid.UUID, exc: Exception) -> None:
    """Record that a page was fetched but its result couldn't be stored, in a
    fresh transaction (the failed one is rolled back first), releasing the
    claim so the URL doesn't hold one of its source's concurrency slots."""
    db.rollback()
    url_row = db.get(JobPostingUrl, url_id, with_for_update=True)
    if url_row is None or url_row.scan_status != ScanStatus.PENDING:
        db.rollback()
        return
    # First line only — a DB error's message embeds the whole failed statement.
    message = str(getattr(exc, "orig", None) or exc)
    detail = (message.splitlines() or [""])[0][:300]
    url_row.scan_status = ScanStatus.FAILED
    url_row.scan_error = _strip_nul(f"Storing the scan result failed ({type(exc).__name__}): {detail}")
    url_row.last_scanned_at = datetime.now(timezone.utc)
    url_row.scan_claimed_at = None
    posting = db.scalar(select(JobPosting).where(JobPosting.url_id == url_id))
    if posting is not None:
        posting.extraction_status = ScanStatus.FAILED
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
    the URL) rather than clobbering good data with a blank one.
    """
    result = scan_job_url(url_row.url)
    now = datetime.now(timezone.utc)
    url_row.last_scanned_at = now
    if result.success:
        url_row.scan_status = ScanStatus.SUCCESS
        url_row.scan_error = None
        _upsert_posting(db, url_row, result, now)
    else:
        url_row.scan_status = ScanStatus.FAILED
        url_row.scan_error = result.error
    db.flush()
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

    # Scraped text is untrusted: a stray NUL byte (Postgres rejects it in
    # text *and* JSONB) or a title longer than its varchar column makes the
    # whole UPDATE fail — and since the failing message is retried, one such
    # page used to be re-scanned forever. Clean it on the way in instead.
    posting.title = _fit(fields["title"], _TITLE_MAX)
    posting.company_name = _fit(fields["company_name"], _COMPANY_NAME_MAX)
    posting.location = _fit(fields["location"], _LOCATION_MAX)
    posting.workplace_type = fields["workplace_type"]
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
