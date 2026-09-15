import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import ApplicationStatus, EmploymentType, ScanStatus, WorkplaceType
from app.models.job_application import UserJobApplication
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services.crawl_sources import register_discovered_board
from app.services.job_llm_extractor import LlmExtraction, extract_with_llm, html_to_text
from app.services.job_queue import enqueue_scan
from app.services.job_scanner import ScanResult, domain_of, normalize_url, scan_job_url, url_hash
from app.services.llm_client import LlmError

logger = logging.getLogger("app.jobs")


def get_or_create_job_posting(
    db: Session,
    raw_url: str,
    submitted_by_user_id: uuid.UUID | None,
    crawl_source_id: uuid.UUID | None = None,
) -> tuple[JobPosting, JobPostingUrl]:
    """Resolve a submitted URL to a (shared, app-wide) JobPosting.

    Dedupes on the normalized URL's hash. The JobPosting row itself always
    exists by the time this returns — for a brand-new URL it's created here
    as an empty PENDING shell and the real scan is queued (see
    process_scan_job, run by the worker) rather than run inline, since
    fetching a page plus an LLM call can take well over a minute, far too
    long to hold open inside a request. Callers distinguish "queued" from
    "actually scanned" via posting.extraction_status, not via None.
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
        register_discovered_board(db, raw_url)
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
    """Worker-side entry point: fetch, extract, and store a JobPosting for
    the given JobPostingUrl. Guards against Pub/Sub's at-least-once delivery
    re-running (and re-billing) a scan that already completed.

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

    result = scan_job_url(url_row.url)
    now = datetime.now(timezone.utc)
    url_row.scan_status = ScanStatus.SUCCESS if result.success else ScanStatus.FAILED
    url_row.scan_error = result.error
    url_row.last_scanned_at = now
    _upsert_posting(db, url_row, result, now)
    db.flush()


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

    posting.title = fields["title"]
    posting.company_name = fields["company_name"]
    posting.location = fields["location"]
    posting.workplace_type = fields["workplace_type"]
    posting.employment_type = fields["employment_type"]
    posting.salary_min = fields["salary_min"]
    posting.salary_max = fields["salary_max"]
    posting.salary_currency = fields["salary_currency"]
    posting.description = result.description
    posting.extracted_fields = _merge_extracted_fields(result, llm_extraction)
    posting.raw_source = {"html_excerpt": result.raw_html_excerpt} if result.raw_html_excerpt else None
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
