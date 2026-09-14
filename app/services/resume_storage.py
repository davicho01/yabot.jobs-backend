import logging

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import settings

logger = logging.getLogger("app.resume_storage")

# Lazily constructed — building a client resolves credentials/endpoint
# immediately, which would fail at import time (breaking `uvicorn main:app`
# for anyone without storage configured yet) if built eagerly at module
# load, same reasoning as app.services.job_queue's lazy Pub/Sub clients.
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=settings.resume_storage_endpoint_url,
            region_name=settings.resume_storage_region,
            aws_access_key_id=settings.resume_storage_access_key_id,
            aws_secret_access_key=settings.resume_storage_secret_access_key,
            # Path-style addressing (bucket in the URL path, not a subdomain)
            # is required for MinIO/local endpoints; real AWS S3 accepts it too.
            config=Config(s3={"addressing_style": "path"}),
        )
    return _client


def ensure_bucket_exists() -> None:
    """Idempotently create the resume bucket if it doesn't exist yet.

    Safe to call every time the API starts: against local MinIO this means
    zero manual setup; against real S3 where the bucket already exists,
    the "already owned" error is caught and ignored.
    """
    client = _get_client()
    try:
        client.head_bucket(Bucket=settings.resume_storage_bucket)
    except ClientError:
        try:
            client.create_bucket(Bucket=settings.resume_storage_bucket)
            logger.info("Created resume storage bucket %s", settings.resume_storage_bucket)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                raise


def upload_file(key: str, data: bytes, content_type: str) -> None:
    _get_client().put_object(Bucket=settings.resume_storage_bucket, Key=key, Body=data, ContentType=content_type)


def download_file(key: str) -> bytes:
    response = _get_client().get_object(Bucket=settings.resume_storage_bucket, Key=key)
    return response["Body"].read()


def delete_file(key: str) -> None:
    _get_client().delete_object(Bucket=settings.resume_storage_bucket, Key=key)
