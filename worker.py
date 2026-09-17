"""Pull-worker process for scanning job posting URLs.

Runs alongside the API (`uvicorn main:app`) as a separate process: the API
publishes a scan request to Pub/Sub when a new URL is submitted and returns
immediately (see app.api.routes.jobs); this process consumes those requests
and does the actual fetch/extract/store work, which can take well over a
minute per URL (page fetch + optional ScraperAPI fallback + LLM extraction).

Local dev: point at the Pub/Sub emulator (see docker-compose.yml) by setting
PUBSUB_EMULATOR_HOST=localhost:8085 before running this. Production: point at
a real GCP project via GCP_PROJECT_ID + GOOGLE_APPLICATION_CREDENTIALS (or
ambient credentials if running on GCP compute) — same code either way.

Usage: python worker.py
"""

import base64
import json
import logging
import uuid

from google.cloud import pubsub_v1

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.job_queue import ensure_topic_and_subscription, subscriber_client, subscription_path
from app.services.jobs import process_scan_job

logging.basicConfig(level=logging.INFO)
# httpx logs the full request URL (including query params) at INFO level.
# ScraperAPI's key travels in the URL's query string, not a header, so
# leaving this at INFO would print it in the clear on every fallback fetch.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("app.worker")

# Bounds how many scans (each doing network I/O + possibly an LLM call) this
# worker process handles concurrently, so a burst of submissions doesn't
# exhaust DB connections or blow past provider rate limits.
_MAX_CONCURRENT_MESSAGES = 10


def _handle_message(message: pubsub_v1.subscriber.message.Message) -> None:
    logger.info("Received message %s (%d bytes)", message.message_id, len(message.data))
    try:
        payload = json.loads(message.data.decode("utf-8"))
        url_id = uuid.UUID(payload["url_id"])
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.error("Malformed scan message %s, dropping: %s", message.message_id, exc)
        message.ack()  # not retryable — the payload will never become valid
        return

    logger.info("Processing scan job for url_id=%s (message %s)", url_id, message.message_id)
    db = SessionLocal()
    try:
        process_scan_job(db, url_id)
        db.commit()
        message.ack()
        logger.info("Finished scan job for url_id=%s (message %s)", url_id, message.message_id)
    except Exception:
        db.rollback()
        logger.exception("Scan job for url_id=%s failed unexpectedly; will retry.", url_id)
        message.nack()
    finally:
        db.close()


def main() -> None:
    logger.info("Starting worker (project=%s)...", settings.gcp_project_id)
    ensure_topic_and_subscription()
    subscriber = subscriber_client()
    flow_control = pubsub_v1.types.FlowControl(max_messages=_MAX_CONCURRENT_MESSAGES)

    future = subscriber.subscribe(subscription_path(), callback=_handle_message, flow_control=flow_control)
    logger.info("Worker ready — listening for scan jobs on %s", subscription_path())

    try:
        future.result()
    except KeyboardInterrupt:
        logger.info("Shutdown requested, cancelling subscription...")
        future.cancel()
        future.result()  # wait for the cancellation to complete
        logger.info("Worker stopped.")


def handle_scan_request(cloud_event) -> None:
    """Cloud Functions (2nd gen) Pub/Sub entry point.

    Prod deploys this instead of running main()'s pull loop: GCP invokes it
    once per message published to job-scan-requests and scales to zero
    between messages, instead of a worker pool instance running 24/7 to
    poll for work. No functions_framework/cloudevents import here — same
    reasoning as crawl_dispatcher.py's dispatch(): the buildpack wraps this
    by signature at deploy time, so keeping it undecorated means this file
    still imports cleanly for local dev (`python worker.py`, see main()
    below) without functions-framework installed.
    """
    data = base64.b64decode(cloud_event.data["message"]["data"])
    try:
        payload = json.loads(data.decode("utf-8"))
        url_id = uuid.UUID(payload["url_id"])
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.error("Malformed scan message, dropping: %s", exc)
        return  # not retryable — returning normally acks the message

    logger.info("Processing scan job for url_id=%s", url_id)
    db = SessionLocal()
    try:
        process_scan_job(db, url_id)
        db.commit()
        logger.info("Finished scan job for url_id=%s", url_id)
    except Exception:
        db.rollback()
        logger.exception("Scan job for url_id=%s failed unexpectedly; will retry.", url_id)
        raise  # re-raise so the Pub/Sub trigger retries the event
    finally:
        db.close()


if __name__ == "__main__":
    main()
