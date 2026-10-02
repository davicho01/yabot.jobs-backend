"""Where the static SEO pages (app.services.static_pages,
app.services.static_job_pages) get written: the frontend's S3 bucket in
prod, or a local directory in dev — which the frontend's Vite dev server
serves the same way CloudFront serves the bucket (a real file wins,
everything else falls through to the SPA). See vite.config.ts.

Picked by settings, in this order: seo_pages_output_dir, then
seo_pages_bucket. Neither set is an error rather than a silent default, so a
local run can never end up writing into the production bucket.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import boto3
from botocore.client import Config

from app.core.config import settings

logger = logging.getLogger("app.page_store")


class PageStoreNotConfigured(RuntimeError):
    pass


class PageStore(Protocol):
    def put(self, key: str, body: str, content_type: str, cache_control: str | None = None) -> None: ...

    def get(self, key: str) -> bytes | None:
        """The object's bytes, or None if it doesn't exist."""
        ...


class S3PageStore:
    def __init__(self, bucket: str):
        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            region_name=settings.seo_pages_region,
            aws_access_key_id=settings.resume_storage_access_key_id,
            aws_secret_access_key=settings.resume_storage_secret_access_key,
            config=Config(s3={"addressing_style": "virtual"}),
        )

    def put(self, key: str, body: str, content_type: str, cache_control: str | None = None) -> None:
        extra = {"CacheControl": cache_control} if cache_control else {}
        self._client.put_object(
            Bucket=self.bucket, Key=key, Body=body.encode("utf-8"), ContentType=content_type, **extra
        )

    def get(self, key: str) -> bytes | None:
        try:
            return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self._client.exceptions.NoSuchKey:
            return None


class LocalDirPageStore:
    """Writes each key as a file under `root`. An extensionless page key
    (jobs/us, job/<id>) becomes <key>/index.html: S3 happily holds both
    jobs/us and jobs/us/sales as objects, but a filesystem can't have
    jobs/us be a file and a directory at once. vite.config.ts's dev
    middleware resolves a request path the same way."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        if "." not in key.rsplit("/", 1)[-1]:
            key = f"{key}/index.html"
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"Key escapes the output directory: {key!r}")
        return path

    def put(self, key: str, body: str, content_type: str, cache_control: str | None = None) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None


_store: PageStore | None = None


def get_page_store() -> PageStore:
    global _store
    if _store is None:
        if settings.seo_pages_output_dir:
            _store = LocalDirPageStore(settings.seo_pages_output_dir)
            logger.info("Writing static pages to %s.", _store.root)
        elif settings.seo_pages_bucket:
            _store = S3PageStore(settings.seo_pages_bucket)
        else:
            raise PageStoreNotConfigured(
                "Set SEO_PAGES_OUTPUT_DIR (local dev) or SEO_PAGES_BUCKET (prod) to publish static pages, "
                "or pass --dry-run."
            )
    return _store
