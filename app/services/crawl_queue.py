import json
import logging
import uuid

from google.api_core.exceptions import AlreadyExists
from google.cloud import pubsub_v1

from app.core.config import settings

logger = logging.getLogger("app.crawl_queue")

# Separate topic/subscription from app.services.job_queue's job-scan queue —
# crawl-source dispatch fan-out shouldn't share a queue with individual
# job-scan requests. Same lazy-init discipline as job_queue.py: building a
# client opens a gRPC channel and resolves credentials immediately, which
# would fail at import time (breaking `uvicorn main:app` for anyone without
# GCP creds/emulator configured) if these were built eagerly at module load.
_publisher: pubsub_v1.PublisherClient | None = None
_subscriber: pubsub_v1.SubscriberClient | None = None


def _get_publisher() -> pubsub_v1.PublisherClient:
    global _publisher
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
    return _publisher


def _get_subscriber() -> pubsub_v1.SubscriberClient:
    global _subscriber
    if _subscriber is None:
        _subscriber = pubsub_v1.SubscriberClient()
    return _subscriber


def _topic_path() -> str:
    return pubsub_v1.PublisherClient.topic_path(settings.gcp_project_id, settings.pubsub_crawl_topic_id)


def _subscription_path() -> str:
    return pubsub_v1.SubscriberClient.subscription_path(
        settings.gcp_project_id, settings.pubsub_crawl_subscription_id
    )


def ensure_topic_and_subscription() -> None:
    """Idempotently create the topic/subscription if they don't exist yet.

    Safe to call every time crawl_dispatcher.py or crawl_worker.py starts:
    against the local emulator this means zero manual `gcloud` provisioning;
    against a real GCP project where they already exist, AlreadyExists is
    caught and ignored.
    """
    topic_path = _topic_path()
    subscription_path = _subscription_path()

    try:
        _get_publisher().create_topic(name=topic_path)
        logger.info("Created Pub/Sub topic %s", topic_path)
    except AlreadyExists:
        pass

    try:
        _get_subscriber().create_subscription(name=subscription_path, topic=topic_path)
        logger.info("Created Pub/Sub subscription %s", subscription_path)
    except AlreadyExists:
        pass


def enqueue_crawl(source_id: uuid.UUID) -> None:
    """Publish a crawl request for the given CrawlSource id.

    Blocks briefly until the publish is confirmed, so a failure raises here
    instead of silently dropping the source from this crawl run.
    """
    payload = json.dumps({"source_id": str(source_id)}).encode("utf-8")
    future = _get_publisher().publish(_topic_path(), data=payload)
    future.result(timeout=10)


def subscription_path() -> str:
    return _subscription_path()


def subscriber_client() -> pubsub_v1.SubscriberClient:
    return _get_subscriber()
