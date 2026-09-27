"""Generates the static, crawlable "jobs by sector, by day" pages described
in generate_static_job_pages.py — the job board at /jobs is a client-rendered
React SPA (invisible to search engines), so this publishes plain HTML
straight into the frontend's S3 bucket/CloudFront distribution instead.

Everything here is pure (query + render + build payloads); generate_static_job_pages.py
is the only caller and is what actually touches S3/CloudFront, so this module
stays testable without any network access.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import boto3
from botocore.client import Config
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import JobSector, ScanStatus
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl

logger = logging.getLogger("app.static_pages")

# The single timezone that defines "day" for these pages — the product is
# US-focused, so a UTC day would split jobs posted the same US day across two
# date pages (evening Pacific postings spilling into the next UTC day).
# ZoneInfo (not a fixed offset) so DST transitions are handled correctly.
DAY_BOUNDARY_TZ = ZoneInfo("America/Los_Angeles")

MANIFEST_KEY = "_meta/generated-pages.json"
SITEMAP_KEY = "sitemap-jobs.xml"

# Human-readable slug/display name per sector, in the order they should list
# on a page's "other sectors" footer. UNKNOWN is deliberately excluded — it's
# not a real category and would look spammy as a page of its own.
_SECTOR_INFO: list[tuple[JobSector, str, str]] = [
    (JobSector.ENGINEERING_TECH, "engineering-tech", "Engineering & Technology"),
    (JobSector.ENGINEERING_TRADITIONAL, "engineering-traditional", "Traditional Engineering"),
    (JobSector.SALES, "sales", "Sales"),
    (JobSector.MARKETING, "marketing", "Marketing"),
    (JobSector.FINANCE_ACCOUNTING, "finance-accounting", "Finance & Accounting"),
    (JobSector.HR, "hr", "Human Resources"),
    (JobSector.OPERATIONS_MANUFACTURING, "operations-manufacturing", "Operations & Manufacturing"),
    (JobSector.CUSTOMER_SUPPORT, "customer-support", "Customer Support"),
    (JobSector.LEGAL, "legal", "Legal"),
    (JobSector.HEALTHCARE, "healthcare", "Healthcare"),
    (JobSector.DESIGN_PRODUCT, "design-product", "Design & Product"),
    (JobSector.EXECUTIVE, "executive", "Executive"),
    (JobSector.ADMINISTRATIVE_OFFICE, "administrative-office", "Administrative & Office"),
    (JobSector.SERVICE_TRADES, "service-trades", "Service & Trades"),
]
SECTOR_SLUGS: dict[JobSector, str] = {sector: slug for sector, slug, _ in _SECTOR_INFO}
SECTOR_DISPLAY_NAMES: dict[JobSector, str] = {sector: name for sector, _, name in _SECTOR_INFO}
SECTORS_BY_SLUG: dict[str, JobSector] = {slug: sector for sector, slug, _ in _SECTOR_INFO}

_TEMPLATE_ENV = Environment(
    loader=FileSystemLoader(str(Path(__file__).resolve().parent.parent.parent / "templates" / "seo")),
    autoescape=select_autoescape(["html.jinja"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def day_bounds_utc(local_day: date) -> tuple[datetime, datetime]:
    """(start, end) UTC instants spanning `local_day`'s midnight-to-midnight
    in DAY_BOUNDARY_TZ — what a JobPostingUrl.created_at range filter compares
    against, since that column is stored in UTC."""
    start_local = datetime(local_day.year, local_day.month, local_day.day, tzinfo=DAY_BOUNDARY_TZ)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


@dataclass
class JobRow:
    url_id: str
    title: str
    company_name: str | None
    location: str | None
    posted_at_local: datetime
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None

    @property
    def salary_display(self) -> str | None:
        """"$120,000 - $150,000" (or a single value when only one bound is
        known) — None when the posting lists no pay at all, in which case the
        job card just omits it rather than showing a placeholder."""
        if self.salary_min is None and self.salary_max is None:
            return None
        symbol = "$" if not self.salary_currency or self.salary_currency == "USD" else ""
        suffix = "" if symbol else f" {self.salary_currency}"

        def fmt(n: int) -> str:
            return f"{symbol}{n:,}{suffix}"

        if self.salary_min is not None and self.salary_max is not None and self.salary_min != self.salary_max:
            return f"{fmt(self.salary_min)} – {fmt(self.salary_max)}"
        return fmt(self.salary_max if self.salary_max is not None else self.salary_min)


def jobs_for_sector_day(db: Session, sector: JobSector, local_day: date) -> list[JobRow]:
    """Every canonical, successfully-scanned, unflagged posting in `sector`
    whose JobPostingUrl.created_at falls in `local_day` (DAY_BOUNDARY_TZ),
    newest first. Same base filter as build_job_search_statement
    (app.services.jobs) — a job appears here iff it would also show up in a
    normal /jobs search for this sector, nothing looser."""
    start_utc, end_utc = day_bounds_utc(local_day)
    stmt = (
        select(JobPostingUrl, JobPosting)
        .join(JobPosting, JobPosting.url_id == JobPostingUrl.id)
        .where(
            JobPosting.extraction_status == ScanStatus.SUCCESS,
            JobPosting.title.is_not(None),
            JobPosting.primary_posting_id.is_(None),
            JobPosting.sector == sector,
            JobPostingUrl.flagged_at.is_(None),
            JobPostingUrl.created_at >= start_utc,
            JobPostingUrl.created_at < end_utc,
        )
        .order_by(JobPostingUrl.created_at.desc())
    )
    rows = db.execute(stmt).all()
    return [
        JobRow(
            url_id=str(url_row.id),
            title=posting.title,
            company_name=posting.company_name,
            location=posting.location,
            posted_at_local=url_row.created_at.astimezone(DAY_BOUNDARY_TZ),
            salary_min=posting.salary_min,
            salary_max=posting.salary_max,
            salary_currency=posting.salary_currency,
        )
        for url_row, posting in rows
    ]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_day_page(
    *,
    sector: JobSector,
    local_day: date,
    jobs: list[JobRow],
    generated_at: datetime,
    manifest: dict,
) -> str:
    slug = SECTOR_SLUGS[sector]
    template = _TEMPLATE_ENV.get_template("sector_day.html.jinja")
    dates_for_sector = sorted(manifest.get(slug, {}))
    day_str = local_day.isoformat()
    idx = dates_for_sector.index(day_str) if day_str in dates_for_sector else -1
    prev_day = dates_for_sector[idx - 1] if idx > 0 else None
    next_day = dates_for_sector[idx + 1] if 0 <= idx < len(dates_for_sector) - 1 else None
    return template.render(
        sector_slug=slug,
        sector_name=SECTOR_DISPLAY_NAMES[sector],
        date_display=local_day.strftime("%B %-d, %Y"),
        date_iso=day_str,
        jobs=jobs,
        job_count=len(jobs),
        generated_at_display=generated_at.astimezone(DAY_BOUNDARY_TZ).strftime("%-I:%M %p"),
        prev_day=prev_day,
        next_day=next_day,
        base_url=settings.seo_pages_base_url,
        other_sectors=[(name, slug) for sec, slug, name in _SECTOR_INFO if sec != sector],
        job_ld_json=_build_job_ld_json(jobs, day_str),
    )


def _build_job_ld_json(jobs: list[JobRow], day_str: str) -> str:
    """A JSON-LD ItemList of JobPosting entries, as a pre-escaped string ready
    to drop straight into a <script type="application/ld+json"> block. Built
    in Python (not Jinja's own JSON filter — plain jinja2.Environment has no
    `tojson`, that's a Flask addition) so titles/company names with quotes or
    unicode are encoded correctly; "</" is escaped so a title/description
    containing it can't prematurely close the surrounding <script> tag."""
    item_list = []
    for i, job in enumerate(jobs):
        item = {
            "@type": "JobPosting",
            "title": job.title,
            "hiringOrganization": {"@type": "Organization", "name": job.company_name or ""},
            "jobLocation": {"@type": "Place", "address": job.location or ""},
            "datePosted": day_str,
            "url": f"{settings.seo_pages_base_url}/jobs/{job.url_id}",
        }
        if job.salary_min is not None or job.salary_max is not None:
            value: dict = {"@type": "QuantitativeValue"}
            if job.salary_min is not None:
                value["minValue"] = job.salary_min
            if job.salary_max is not None:
                value["maxValue"] = job.salary_max
            item["baseSalary"] = {
                "@type": "MonetaryAmount",
                "currency": job.salary_currency or "USD",
                "value": value,
            }
        item_list.append({"@type": "ListItem", "position": i + 1, "item": item})
    payload = {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": item_list}
    return json.dumps(payload).replace("</", "<\\/")


def render_sector_index(*, sector: JobSector, dates_with_counts: list[tuple[str, int]]) -> str:
    """`dates_with_counts`: [(YYYY-MM-DD, job_count), ...], any order — sorted
    here newest-first and grouped by month."""
    slug = SECTOR_SLUGS[sector]
    template = _TEMPLATE_ENV.get_template("sector_index.html.jinja")
    ordered = sorted(dates_with_counts, key=lambda pair: pair[0], reverse=True)
    months: dict[str, list[tuple[str, str, int]]] = {}
    for iso_date, count in ordered:
        d = date.fromisoformat(iso_date)
        month_label = d.strftime("%B %Y")
        months.setdefault(month_label, []).append((iso_date, d.strftime("%b %-d, %Y"), count))
    return template.render(
        sector_slug=slug,
        sector_name=SECTOR_DISPLAY_NAMES[sector],
        months=list(months.items()),
        base_url=settings.seo_pages_base_url,
    )


# ---------------------------------------------------------------------------
# Manifest / sitemap
# ---------------------------------------------------------------------------


def add_to_manifest(manifest: dict, sector: JobSector, local_day: date, job_count: int) -> dict:
    """Returns a new manifest with (sector, local_day) recorded against
    `job_count`. `manifest` maps sector-slug -> {"YYYY-MM-DD": job_count} for
    every day ever generated — the count (not just a list of dates) is stored
    so the sector-index page can show "N jobs" per day without re-querying
    the DB for every past date on every run; only today's entry actually
    changes between runs, past ones are frozen once their day closes."""
    slug = SECTOR_SLUGS[sector]
    day_str = local_day.isoformat()
    updated = {k: dict(v) for k, v in manifest.items()}
    updated.setdefault(slug, {})[day_str] = job_count
    return updated


def build_sitemap_xml(manifest: dict) -> str:
    base = settings.seo_pages_base_url
    urls = []
    for slug, days in sorted(manifest.items()):
        urls.append(f"{base}/jobs/{slug}")
        urls.extend(f"{base}/jobs/{slug}/{day}" for day in sorted(days))
    entries = "\n".join(f"  <url><loc>{url}</loc></url>" for url in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n"
        "</urlset>\n"
    )


# ---------------------------------------------------------------------------
# S3 / CloudFront — lazy client, same reasoning as app.services.resume_storage
# ---------------------------------------------------------------------------

_s3_client = None


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            region_name=settings.seo_pages_region,
            aws_access_key_id=settings.resume_storage_access_key_id,
            aws_secret_access_key=settings.resume_storage_secret_access_key,
            config=Config(s3={"addressing_style": "virtual"}),
        )
    return _s3_client


def upload_html(key: str, html_content: str) -> None:
    _get_s3_client().put_object(
        Bucket=settings.seo_pages_bucket, Key=key, Body=html_content.encode("utf-8"), ContentType="text/html; charset=utf-8"
    )


def read_manifest() -> dict:
    try:
        response = _get_s3_client().get_object(Bucket=settings.seo_pages_bucket, Key=MANIFEST_KEY)
        return json.loads(response["Body"].read())
    except _get_s3_client().exceptions.NoSuchKey:
        return {}


def write_manifest(manifest: dict) -> None:
    _get_s3_client().put_object(
        Bucket=settings.seo_pages_bucket,
        Key=MANIFEST_KEY,
        Body=json.dumps(manifest, sort_keys=True).encode("utf-8"),
        ContentType="application/json",
    )


@dataclass
class GenerationResult:
    generated_at: datetime
    target_date: date
    sector_job_counts: dict[str, int]  # slug -> job count, only sectors with >=1 job today
    manifest: dict


def generate_for_date(db: Session, target_date: date, *, dry_run: bool = False) -> GenerationResult:
    """The whole run: for every sector with at least one job on `target_date`
    (DAY_BOUNDARY_TZ), write/overwrite that day's page, refresh the sector's
    index page, update the manifest, and rebuild sitemap-jobs.xml — then
    invalidate exactly the CloudFront paths touched. Sectors with zero jobs
    today are skipped entirely (no page, no manifest entry) rather than
    publishing a thin/empty page.

    Safe to call repeatedly for the same date (idempotent overwrites) — this
    is exactly how same-day pages get "updated as of HH:MM" freshness: call
    it again a couple hours later and it just re-renders with whatever's now
    in the DB. dry_run skips every S3/CloudFront call and returns what would
    have been written.
    """
    generated_at = datetime.now(timezone.utc)
    manifest = {} if dry_run else read_manifest()
    touched_paths: list[str] = []
    sector_job_counts: dict[str, int] = {}

    for sector in SECTOR_SLUGS:
        jobs = jobs_for_sector_day(db, sector, target_date)
        if not jobs:
            continue
        slug = SECTOR_SLUGS[sector]
        sector_job_counts[slug] = len(jobs)
        manifest = add_to_manifest(manifest, sector, target_date, len(jobs))
        day_html = render_day_page(sector=sector, local_day=target_date, jobs=jobs, generated_at=generated_at, manifest=manifest)
        day_key = f"jobs/{slug}/{target_date.isoformat()}"
        index_html = render_sector_index(sector=sector, dates_with_counts=list(manifest[slug].items()))
        index_key = f"jobs/{slug}"
        logger.info("%s: %d job(s) for %s.", slug, len(jobs), target_date.isoformat())
        if not dry_run:
            upload_html(day_key, day_html)
            upload_html(index_key, index_html)
        touched_paths.extend([f"/{day_key}", f"/{index_key}"])

    if not dry_run and sector_job_counts:
        write_manifest(manifest)
        _get_s3_client().put_object(
            Bucket=settings.seo_pages_bucket,
            Key=SITEMAP_KEY,
            Body=build_sitemap_xml(manifest).encode("utf-8"),
            ContentType="application/xml",
        )
        touched_paths.append(f"/{SITEMAP_KEY}")
        invalidate_paths(touched_paths)

    return GenerationResult(
        generated_at=generated_at, target_date=target_date, sector_job_counts=sector_job_counts, manifest=manifest
    )


def invalidate_paths(paths: list[str]) -> None:
    """Invalidate exactly the CloudFront paths touched this run — needed
    because these pages get overwritten intraday and an edge cache would
    otherwise mask the update until its TTL expires. No-op if no
    distribution is configured (e.g. local dev)."""
    if not paths or not settings.seo_pages_cloudfront_distribution_id:
        return
    client = boto3.client(
        "cloudfront",
        aws_access_key_id=settings.resume_storage_access_key_id,
        aws_secret_access_key=settings.resume_storage_secret_access_key,
    )
    client.create_invalidation(
        DistributionId=settings.seo_pages_cloudfront_distribution_id,
        InvalidationBatch={
            "Paths": {"Quantity": len(paths), "Items": paths},
            "CallerReference": f"generate-static-job-pages-{datetime.now(timezone.utc).isoformat()}",
        },
    )
