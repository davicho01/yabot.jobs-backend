"""Pull-worker process for the daily discovery crawl.

Consumes crawl-source-requests messages published by crawl_dispatcher.py:
for each, lists every current job URL for that company's board (via the
ATS's own public API — see app.services.ats_adapters) and registers the
whole list at once (see app.services.jobs.bulk_register_discovered_urls) —
a handful of round-trips for the whole board rather than one per URL, which
is what the get_or_create_job_posting flow user submissions go through is
built for one URL at a time, not a board that can list thousands. Genuinely
new URLs are left PENDING, and once the whole board has been recorded this
worker wakes that source's throttled scan lanes on the *existing* job-scan
topic (see app.services.scan_claims / worker.py) — never one message per
URL, so a site is only ever fetched from CrawlSource.max_concurrent_scans
pages at a time. Already-known URLs are a no-op.

Run multiple instances of this process to crawl many companies in parallel —
it's a normal Pub/Sub pull subscription, so messages are split across
whichever instances are running (same competing-consumer pattern worker.py
already uses).

Local dev: point at the Pub/Sub emulator (see docker-compose.yml) by setting
PUBSUB_EMULATOR_HOST=localhost:8085 before running this. Production: point at
a real GCP project via GCP_PROJECT_ID + GOOGLE_APPLICATION_CREDENTIALS (or
ambient credentials if running on GCP compute) — same code either way.

Usage: python crawl_worker.py
"""

import base64
import json
import logging
import uuid
from datetime import datetime, timezone

from google.cloud import pubsub_v1
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.log_config import configure_logging
from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.services.ats_adapters import list_job_urls
from app.services.coverage_monitor import update_coverage
from app.services.crawl_queue import ensure_topic_and_subscription, subscriber_client, subscription_path
from app.services.job_queue import enqueue_source_scan
from app.services.jobs import bulk_register_discovered_urls, record_board_presence
from app.services.scan_claims import has_unclaimed_pending

configure_logging()
logger = logging.getLogger("app.crawl_worker")

# Bounds how many sources this process crawls concurrently.
_MAX_CONCURRENT_MESSAGES = 5


def _crawl_source(db: Session, source_id: uuid.UUID) -> None:
    source = db.get(CrawlSource, source_id)
    if source is None:
        logger.warning("Crawl job for unknown source_id=%s; skipping.", source_id)
        return

    try:
        urls = list_job_urls(source.ats_type, source.board_url)
    except Exception as exc:
        # A bad board_key or an ATS outage isn't retryable by nacking —
        # record it and move on, same as any other scan failure in this app.
        logger.warning("Failed to list jobs for %s: %s", source.name, exc)
        source.last_error = str(exc)
        source.last_crawled_at = datetime.now(timezone.utc)
        source.crawl_claimed_at = None  # done with this attempt; dispatchable again next cycle
        return

    # Read up front: the rollback in the failure path below expires every
    # loaded attribute.
    source_id, source_name, lanes = source.id, source.name, source.max_concurrent_scans

    # One SELECT for which of these are already known, one bulk INSERT for
    # whatever's new, instead of a round-trip (and a commit, for a new URL)
    # per URL — see bulk_register_discovered_urls. A large board can list
    # thousands of URLs; that used to mean thousands of sequential
    # round-trips to Cloud SQL in a single invocation.
    registered = True
    try:
        failed = bulk_register_discovered_urls(db, urls, source_id)
    except Exception as exc:
        # Same reasoning as the old per-URL loop's except: don't let this
        # crawl's stats update below be skipped just because registration
        # failed, and don't leave the session poisoned for it.
        db.rollback()
        registered = False
        failed = len(urls)
        logger.warning("Failed to register discovered URLs for %s: %s", source_name, exc)

    source = db.get(CrawlSource, source_id)
    if source is None:  # deleted while this crawl was running
        return
    source.last_crawled_at = datetime.now(timezone.utc)
    source.last_error = f"{failed} of {len(urls)} discovered URL(s) failed to process." if failed else None
    source.crawl_claimed_at = None  # done; dispatchable again next cycle
    update_coverage(source, len(urls))
    # Only a crawl that plausibly saw the whole board may close the jobs it
    # didn't list: a non-empty listing, fully registered, and not below the
    # coverage monitor's baseline (a broken adapter or truncated listing).
    healthy = bool(urls) and registered and not failed and source.coverage_low_streak == 0
    closed = record_board_presence(db, source_id, urls, healthy=healthy)
    db.commit()  # stats persisted before waking lanes (has_unclaimed_pending ends its transaction)
    logger.info(
        "Crawled %s: %d job URL(s) discovered (%d failed), %d closed%s.",
        source_name, len(urls), failed, closed, "" if healthy else " (unhealthy crawl: closing skipped)",
    )

    _wake_source_lanes(db, source_id, source_name, lanes)


def _wake_source_lanes(db: Session, source_id: uuid.UUID, source_name: str, lanes: int) -> None:
    """Start up to `lanes` throttled scan lanes for this source if it has
    anything left to scan. A publish failure is logged, not raised: the URLs
    are already safely PENDING, and crawl_dispatcher's periodic sweep wakes
    any source that still has pending work — nacking here would just re-crawl
    the whole board for nothing."""
    try:
        if has_unclaimed_pending(db, source_id):
            enqueue_source_scan(source_id, lanes=lanes)
            logger.info("Woke %d scan lane(s) for %s.", lanes, source_name)
    except Exception:
        logger.exception("Failed to wake scan lanes for %s; the dispatcher sweep will retry.", source_name)


def _handle_message(message: pubsub_v1.subscriber.message.Message) -> None:
    logger.info("Received message %s (%d bytes)", message.message_id, len(message.data))
    try:
        payload = json.loads(message.data.decode("utf-8"))
        source_id = uuid.UUID(payload["source_id"])
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.error("Malformed crawl message %s, dropping: %s", message.message_id, exc)
        message.ack()  # not retryable — the payload will never become valid
        return

    logger.info("Processing crawl for source_id=%s (message %s)", source_id, message.message_id)
    db = SessionLocal()
    try:
        _crawl_source(db, source_id)
        db.commit()
        message.ack()
        logger.info("Finished crawl for source_id=%s (message %s)", source_id, message.message_id)
    except Exception:
        db.rollback()
        logger.exception("Crawl for source_id=%s failed unexpectedly; will retry.", source_id)
        message.nack()
    finally:
        db.close()


def main() -> None:
    logger.info("Starting crawl worker (project=%s)...", settings.gcp_project_id)
    ensure_topic_and_subscription()
    subscriber = subscriber_client()
    flow_control = pubsub_v1.types.FlowControl(max_messages=_MAX_CONCURRENT_MESSAGES)

    future = subscriber.subscribe(subscription_path(), callback=_handle_message, flow_control=flow_control)
    logger.info("Crawl worker ready — listening for crawl jobs on %s", subscription_path())

    try:
        future.result()
    except KeyboardInterrupt:
        logger.info("Shutdown requested, cancelling subscription...")
        future.cancel()
        future.result()  # wait for the cancellation to complete
        logger.info("Crawl worker stopped.")


def handle_crawl_request(event, context) -> None:
    """Cloud Functions (2nd gen) Pub/Sub entry point.

    Prod deploys this instead of running main()'s pull loop: GCP invokes it
    once per message published to crawl-source-requests and scales to zero
    between messages, instead of a worker pool instance running 24/7 to
    poll for work. No functions_framework/cloudevents import here — same
    reasoning as worker.py's handle_scan_request(): the buildpack wraps this
    by signature at deploy time. Undecorated + this two-arg (event, context)
    signature is what the buildpack actually invokes for a --trigger-topic
    deploy (confirmed against a real deploy — a single-arg CloudEvent-typed
    signature gets called as function(data, context) and blows up with
    "takes 1 positional argument but 2 were given"), and it keeps this file
    importable for local dev (`python crawl_worker.py`, see main() below)
    without functions-framework installed.
    """
    data = base64.b64decode(event["data"])
    try:
        payload = json.loads(data.decode("utf-8"))
        source_id = uuid.UUID(payload["source_id"])
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.error("Malformed crawl message, dropping: %s", exc)
        return  # not retryable — returning normally acks the message

    logger.info("Processing crawl for source_id=%s", source_id)
    db = SessionLocal()
    try:
        _crawl_source(db, source_id)
        db.commit()
        logger.info("Finished crawl for source_id=%s", source_id)
    except Exception:
        db.rollback()
        logger.exception("Crawl for source_id=%s failed unexpectedly; will retry.", source_id)
        raise  # re-raise so the Pub/Sub trigger retries the event
    finally:
        db.close()


if __name__ == "__main__":
    main()
