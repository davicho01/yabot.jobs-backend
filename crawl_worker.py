"""Pull-worker process for the daily discovery crawl.

Consumes crawl-source-requests messages published by crawl_dispatcher.py:
for each, lists every current job URL for that company's board (via the
ATS's own public API — see app.services.ats_adapters) and hands each one to
the normal get_or_create_job_posting flow, passing crawl_source_id so it
skips board (re-)registration — that only happens for genuinely
user-submitted URLs, see get_or_create_job_posting. Genuinely new URLs get
queued onto the *existing* job-scan topic and picked up by worker.py;
already-known ones are a no-op.

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
from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.services.ats_adapters import list_job_urls
from app.services.crawl_queue import ensure_topic_and_subscription, subscriber_client, subscription_path
from app.services.jobs import get_or_create_job_posting

logging.basicConfig(level=logging.INFO)
# httpx logs the full request URL (including query params) at INFO level —
# harmless here (ATS APIs need no key), but kept consistent with worker.py.
logging.getLogger("httpx").setLevel(logging.WARNING)
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
        return

    failed = 0
    for url in urls:
        try:
            get_or_create_job_posting(db, url, submitted_by_user_id=None, crawl_source_id=source.id)
        except Exception as exc:
            # One bad URL (malformed link, a transient publish failure, ...)
            # used to propagate out of this function and skip the
            # last_crawled_at update below entirely — and since
            # _handle_message nacks on any exception, a *deterministic*
            # per-URL failure left the source's stats permanently stale
            # across every redelivery. Roll back so this URL's partial work
            # doesn't poison the session for the rest of the batch, then
            # keep going.
            db.rollback()
            failed += 1
            logger.warning("Failed to process discovered URL %s for %s: %s", url, source.name, exc)

    source.last_crawled_at = datetime.now(timezone.utc)
    source.last_error = f"{failed} of {len(urls)} discovered URL(s) failed to process." if failed else None
    logger.info("Crawled %s: %d job URL(s) discovered (%d failed).", source.name, len(urls), failed)


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


def handle_crawl_request(cloud_event) -> None:
    """Cloud Functions (2nd gen) Pub/Sub entry point.

    Prod deploys this instead of running main()'s pull loop: GCP invokes it
    once per message published to crawl-source-requests and scales to zero
    between messages, instead of a worker pool instance running 24/7 to
    poll for work. No functions_framework/cloudevents import here — same
    reasoning as crawl_dispatcher.py's dispatch(): the buildpack wraps this
    by signature at deploy time, so keeping it undecorated means this file
    still imports cleanly for local dev (`python crawl_worker.py`, see
    main() below) without functions-framework installed.
    """
    data = base64.b64decode(cloud_event.data["message"]["data"])
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
