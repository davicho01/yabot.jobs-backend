"""Generates the static, crawlable "jobs by country, sector, and day" pages
described in generate_static_job_pages.py — the job board at /jobs is a
client-rendered React SPA (invisible to search engines), so this publishes
plain HTML straight into the frontend's S3 bucket/CloudFront distribution
instead.

Everything here is pure (query + render + build payloads); generate_static_job_pages.py
is the only caller and is what actually touches S3/CloudFront, so this module
stays testable without any network access.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import boto3
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import JobSector, ScanStatus
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services.company_logos import logo_url_for
from app.services.page_store import get_page_store

logger = logging.getLogger("app.static_pages")

# The single timezone that defines "day" for these pages — the product is
# US-focused, so a UTC day would split jobs posted the same US day across two
# date pages (evening Pacific postings spilling into the next UTC day).
# ZoneInfo (not a fixed offset) so DST transitions are handled correctly.
DAY_BOUNDARY_TZ = ZoneInfo("America/Los_Angeles")

MANIFEST_KEY = "_meta/generated-pages.json"
SITEMAP_KEY = "sitemap-jobs.xml"

# Countries these pages cover — just the US for now (JobPosting.country is an
# ISO2 code; see app.services.geo.resolve_country_for_locations for how it's
# set). Adding a second country later is just adding a row here; nothing
# about the URL scheme, manifest shape, or rendering assumes there's only one.
_COUNTRIES: list[tuple[str, str, str]] = [
    ("us", "US", "United States"),
]
COUNTRY_ISO2_BY_SLUG: dict[str, str] = {slug: iso2 for slug, iso2, _ in _COUNTRIES}
COUNTRY_NAMES: dict[str, str] = {slug: name for slug, _, name in _COUNTRIES}

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
    scanned_at_local: datetime  # when we found it — NOT when the employer posted it
    posted_at: date | None = None  # the employer's own stated posting date, if known
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    # Where this job's own static page lives (no leading slash) — see
    # static_job_pages; set by generate_for_date from the job manifest.
    path: str | None = None

    @property
    def href_path(self) -> str:
        return self.path or f"job/{self.url_id}"
    company_logo_url: str | None = None

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


def jobs_for_sector_day(db: Session, country_iso2: str, sector: JobSector, local_day: date) -> list[JobRow]:
    """Every canonical, successfully-scanned, unflagged `country_iso2` posting
    in `sector` that was actually *posted* on `local_day` (DAY_BOUNDARY_TZ) —
    or, when the employer's posted_at is unknown, whose JobPostingUrl.created_at
    (when we found it) falls in `local_day` instead, so a posting with no
    stated date is still findable somewhere rather than never appearing on
    any page. A known posted_at is never overridden by a different
    created_at date — a job genuinely posted last week that we happen to
    scan today belongs on last week's page, not today's (a known limitation:
    since only today's page is ever regenerated, going forward, a job like
    that — discovered too late for its own day's now-frozen page — won't
    appear anywhere; accepted for now, same trade-off as everything else
    "forward-only" already makes). Same base filter as
    build_job_search_statement (app.services.jobs) plus the country match —
    a job appears here iff it would also show up in a normal /jobs search
    for this sector, nothing looser.

    Ordered by JobPostingUrl.created_at (newest scan first) — not by
    posted_at, deliberately: this page regenerates every 30 minutes as more
    of the day's postings get discovered, and ordering by scan order keeps
    each run's newly-found jobs landing at the top rather than the whole
    list re-shuffling alphabetically (or by posted_at, which many postings
    share) on every regeneration.
    """
    start_utc, end_utc = day_bounds_utc(local_day)
    stmt = (
        select(JobPostingUrl, JobPosting)
        .join(JobPosting, JobPosting.url_id == JobPostingUrl.id)
        .where(
            JobPosting.extraction_status == ScanStatus.SUCCESS,
            JobPosting.title.is_not(None),
            JobPosting.primary_posting_id.is_(None),
            JobPosting.sector == sector,
            JobPosting.country == country_iso2,
            JobPostingUrl.flagged_at.is_(None),
            JobPostingUrl.closed_at.is_(None),
            or_(
                JobPosting.posted_at == local_day,
                and_(
                    JobPosting.posted_at.is_(None),
                    JobPostingUrl.created_at >= start_utc,
                    JobPostingUrl.created_at < end_utc,
                ),
            ),
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
            scanned_at_local=url_row.created_at.astimezone(DAY_BOUNDARY_TZ),
            posted_at=posting.posted_at,
            salary_min=posting.salary_min,
            salary_max=posting.salary_max,
            salary_currency=posting.salary_currency,
            company_logo_url=logo_url_for(posting.company_logo_key),
        )
        for url_row, posting in rows
    ]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_day_page(
    *,
    country_slug: str,
    sector: JobSector,
    local_day: date,
    jobs: list[JobRow],
    generated_at: datetime,
    manifest: dict,
) -> str:
    sector_slug = SECTOR_SLUGS[sector]
    country_name = COUNTRY_NAMES[country_slug]
    template = _TEMPLATE_ENV.get_template("sector_day.html.jinja")
    dates_for_sector = sorted(manifest.get(country_slug, {}).get(sector_slug, {}))
    day_str = local_day.isoformat()
    idx = dates_for_sector.index(day_str) if day_str in dates_for_sector else -1
    prev_day = dates_for_sector[idx - 1] if idx > 0 else None
    next_day = dates_for_sector[idx + 1] if 0 <= idx < len(dates_for_sector) - 1 else None
    return template.render(
        country_slug=country_slug,
        country_name=country_name,
        sector_slug=sector_slug,
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
            "datePosted": job.posted_at.isoformat() if job.posted_at else day_str,
            "url": f"{settings.seo_pages_base_url}/{job.href_path}",
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


def render_sector_index(*, country_slug: str, sector: JobSector, dates_with_counts: list[tuple[str, int]]) -> str:
    """`dates_with_counts`: [(YYYY-MM-DD, job_count), ...], any order — sorted
    here newest-first and grouped by month."""
    sector_slug = SECTOR_SLUGS[sector]
    country_name = COUNTRY_NAMES[country_slug]
    template = _TEMPLATE_ENV.get_template("sector_index.html.jinja")
    ordered = sorted(dates_with_counts, key=lambda pair: pair[0], reverse=True)
    months: dict[str, list[tuple[str, str, int]]] = {}
    for iso_date, count in ordered:
        d = date.fromisoformat(iso_date)
        month_label = d.strftime("%B %Y")
        months.setdefault(month_label, []).append((iso_date, d.strftime("%b %-d, %Y"), count))
    return template.render(
        country_slug=country_slug,
        country_name=country_name,
        sector_slug=sector_slug,
        sector_name=SECTOR_DISPLAY_NAMES[sector],
        months=list(months.items()),
        base_url=settings.seo_pages_base_url,
    )


def render_country_index(
    *, country_slug: str, sector_totals: list[tuple[str, str, int]], hub_links: dict | None = None
) -> str:
    """`sector_totals`: [(sector_slug, sector_name, total_job_count), ...],
    already ordered how they should list (see sector_totals() below) — this
    just renders, it doesn't re-sort. `hub_links`: {"companies": [...],
    "locations": [...]} of (name, path, count) from
    static_hub_pages.HubPlan.country_links, listed under the sectors."""
    country_name = COUNTRY_NAMES[country_slug]
    template = _TEMPLATE_ENV.get_template("country_index.html.jinja")
    return template.render(
        country_slug=country_slug,
        country_name=country_name,
        sector_totals=sector_totals,
        hub_links=hub_links or {},
        base_url=settings.seo_pages_base_url,
    )


# ---------------------------------------------------------------------------
# Manifest / sitemap
# ---------------------------------------------------------------------------


def add_to_manifest(manifest: dict, country_slug: str, sector: JobSector, local_day: date, job_count: int) -> dict:
    """Returns a new manifest with (country_slug, sector, local_day) recorded
    against `job_count`. `manifest` maps
    country-slug -> sector-slug -> {"YYYY-MM-DD": job_count} for every day
    ever generated — the count (not just a list of dates) is stored so the
    sector-index page can show "N jobs" per day without re-querying the DB
    for every past date on every run; only today's entry actually changes
    between runs, past ones are frozen once their day closes."""
    sector_slug = SECTOR_SLUGS[sector]
    day_str = local_day.isoformat()
    updated = {c: {s: dict(days) for s, days in sectors.items()} for c, sectors in manifest.items()}
    updated.setdefault(country_slug, {}).setdefault(sector_slug, {})[day_str] = job_count
    return updated


def sector_totals(manifest: dict, country_slug: str) -> list[tuple[str, str, int]]:
    """[(sector_slug, sector_name, total_job_count), ...] summed across every
    day ever recorded for country_slug, sorted alphabetically by sector name
    (a navigation page, not a ranking — alphabetical is what's actually easy
    to scan for "find my sector"), skipping any sector with zero jobs (its
    own sector_index page doesn't exist either in that case — see
    generate_for_date — so there'd be nothing to link to)."""
    days_by_sector = manifest.get(country_slug, {})
    totals = []
    for sector, slug, name in _SECTOR_INFO:
        total = sum(days_by_sector.get(slug, {}).values())
        if total:
            totals.append((slug, name, total))
    return sorted(totals, key=lambda entry: entry[1])


def build_sitemap_xml(manifest: dict) -> str:
    # Index pages (no date) are re-rendered from the whole manifest on every
    # generate_for_date run — several times a day per gcloud-deploy.sh's
    # "0 10-22/6 * * *" schedule — so they're genuinely daily-changing. Day
    # pages only get re-touched while local_day == today (add_to_manifest's
    # docstring: "only today's entry actually changes between runs"); once
    # that date is in the past nothing regenerates it, so it's frozen.
    today_str = datetime.now(DAY_BOUNDARY_TZ).date().isoformat()
    base = settings.seo_pages_base_url
    entries = []
    for country_slug, sectors in sorted(manifest.items()):
        entries.append(f"  <url><loc>{base}/jobs/{country_slug}</loc><changefreq>daily</changefreq></url>")
        for sector_slug, days in sorted(sectors.items()):
            entries.append(
                f"  <url><loc>{base}/jobs/{country_slug}/{sector_slug}</loc>"
                "<changefreq>daily</changefreq></url>"
            )
            entries.extend(
                f"  <url><loc>{base}/jobs/{country_slug}/{sector_slug}/{day}</loc>"
                f"<changefreq>{'daily' if day == today_str else 'never'}</changefreq></url>"
                for day in sorted(days)
            )
    body = "\n".join(entries)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n"
        "</urlset>\n"
    )


# ---------------------------------------------------------------------------
# Publishing — through app.services.page_store (S3 in prod, a local
# directory in dev)
# ---------------------------------------------------------------------------


def upload_html(key: str, html_content: str, cache_control: str | None = None) -> None:
    get_page_store().put(key, html_content, "text/html; charset=utf-8", cache_control)


def upload_xml(key: str, xml_content: str) -> None:
    get_page_store().put(key, xml_content, "application/xml")


def read_json(key: str) -> dict | None:
    body = get_page_store().get(key)
    return None if body is None else json.loads(body)


def write_json(key: str, data: dict) -> None:
    get_page_store().put(key, json.dumps(data, sort_keys=True), "application/json")


def read_manifest() -> dict:
    manifest = read_json(MANIFEST_KEY)
    if manifest is None:
        return {}
    if not _is_current_manifest_shape(manifest):
        # Pre-country-segment manifests are flat {sector_slug: {date: count}} —
        # one level shallower than today's {country_slug: {sector_slug: {date:
        # count}}}. Rather than migrate it (and rather than requiring a manual
        # delete of the old S3 object before this code can run), just treat it
        # as absent: the next run starts a fresh manifest/sitemap under the new
        # shape, and the old country-less pages it referenced are already the
        # ones deliberately left to go stale, unlinked from anywhere new.
        logger.warning("Manifest at %s isn't in the current country-nested shape; starting fresh.", MANIFEST_KEY)
        return {}
    return manifest


def _is_current_manifest_shape(manifest: dict) -> bool:
    """True for {country_slug: {sector_slug: {date: count}}}, checked just
    deep enough to tell it apart from the older flat {sector_slug: {date:
    count}} shape (whose second level holds ints, not dicts)."""
    for sectors in manifest.values():
        if not isinstance(sectors, dict):
            return False
        for days in sectors.values():
            if not isinstance(days, dict):
                return False
    return True


def write_manifest(manifest: dict) -> None:
    write_json(MANIFEST_KEY, manifest)


@dataclass
class GenerationResult:
    generated_at: datetime
    target_date: date
    # "us/engineering-tech" -> job count, only country/sector combos with >=1 job today
    job_counts: dict[str, int]
    manifest: dict
    # CloudFront paths this run wrote (empty for a dry run).
    touched_paths: list[str] = field(default_factory=list)


def generate_for_date(
    db: Session,
    target_date: date,
    *,
    dry_run: bool = False,
    invalidate: bool = True,
    job_paths: dict[str, str] | None = None,
    hub_links: dict | None = None,
) -> GenerationResult:
    """The whole run: for every country x sector with at least one job on
    `target_date` (DAY_BOUNDARY_TZ), write/overwrite that day's page, refresh
    the sector's index page, update the manifest, refresh each country's
    sector-navigation hub page (jobs/{country}), and rebuild
    sitemap-jobs.xml — then invalidate exactly the CloudFront paths touched.
    A combo with zero jobs today is skipped entirely (no page, no manifest
    entry) rather than publishing a thin/empty page.

    Safe to call repeatedly for the same date (idempotent overwrites) — this
    is exactly how same-day pages get "updated as of HH:MM" freshness: call
    it again a couple hours later and it just re-renders with whatever's now
    in the DB. dry_run skips every S3/CloudFront call and returns what would
    have been written. invalidate=False leaves the CloudFront call to the
    caller (generate_static_job_pages.py folds this run's paths in with the
    per-job pages' into one invalidation) — see result.touched_paths.
    """
    generated_at = datetime.now(timezone.utc)
    manifest = {} if dry_run else read_manifest()
    touched_paths: list[str] = []
    job_counts: dict[str, int] = {}

    for country_slug, country_iso2, _ in _COUNTRIES:
        for sector in SECTOR_SLUGS:
            jobs = jobs_for_sector_day(db, country_iso2, sector, target_date)
            if job_paths is not None:
                # Each job links to wherever its page was published
                # (static_job_pages); one not published yet (over a run's
                # render cap) links to the app's own page for it instead.
                for job in jobs:
                    job.path = job_paths.get(job.url_id) or f"jobs/{job.url_id}"
            if not jobs:
                continue
            sector_slug = SECTOR_SLUGS[sector]
            job_counts[f"{country_slug}/{sector_slug}"] = len(jobs)
            manifest = add_to_manifest(manifest, country_slug, sector, target_date, len(jobs))
            day_html = render_day_page(
                country_slug=country_slug, sector=sector, local_day=target_date, jobs=jobs,
                generated_at=generated_at, manifest=manifest,
            )
            day_key = f"jobs/{country_slug}/{sector_slug}/{target_date.isoformat()}"
            index_html = render_sector_index(
                country_slug=country_slug, sector=sector,
                dates_with_counts=list(manifest[country_slug][sector_slug].items()),
            )
            index_key = f"jobs/{country_slug}/{sector_slug}"
            logger.info("%s/%s: %d job(s) for %s.", country_slug, sector_slug, len(jobs), target_date.isoformat())
            if not dry_run:
                upload_html(day_key, day_html)
                upload_html(index_key, index_html)
            touched_paths.extend([f"/{day_key}", f"/{index_key}"])

    if not dry_run and job_counts:
        write_manifest(manifest)
        for country_slug, _, _ in _COUNTRIES:
            country_html = render_country_index(
                country_slug=country_slug, sector_totals=sector_totals(manifest, country_slug), hub_links=hub_links
            )
            country_key = f"jobs/{country_slug}"
            upload_html(country_key, country_html)
            touched_paths.append(f"/{country_key}")
        upload_xml(SITEMAP_KEY, build_sitemap_xml(manifest))
        touched_paths.append(f"/{SITEMAP_KEY}")
        if invalidate:
            invalidate_paths(touched_paths)

    return GenerationResult(
        generated_at=generated_at, target_date=target_date, job_counts=job_counts, manifest=manifest,
        touched_paths=touched_paths if not dry_run else [],
    )


def invalidate_paths(paths: list[str]) -> None:
    """Invalidate exactly the CloudFront paths touched this run — needed
    because these pages get overwritten intraday and an edge cache would
    otherwise mask the update until its TTL expires. No-op if no
    distribution is configured (e.g. local dev)."""
    if not paths or not settings.seo_pages_cloudfront_distribution_id:
        return
    logger.info("Invalidating %s.", ", ".join(paths))
    try:
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
    except Exception:
        # The pages and manifest are already written — a failed invalidation
        # only delays them by the edge TTL (see static_job_pages.CACHE_CONTROL),
        # so it mustn't fail the run.
        logger.exception("CloudFront invalidation of %d path(s) failed.", len(paths))


def collapse_invalidation_paths(paths: list[str]) -> list[str]:
    """One CloudFront wildcard per page family instead of a path per page —
    a wildcard counts as a single path against the 1,000-free-a-month
    allowance however many objects it clears, and one admin source rescan
    can change thousands of /job/ pages. Anything outside those families
    (the sitemaps) is kept as-is."""
    collapsed: list[str] = []
    for path in paths:
        for prefix in ("/jobs/", "/job/"):
            if path.startswith(prefix):
                path = f"{prefix}*"
                break
        if path not in collapsed:
            collapsed.append(path)
    return collapsed
