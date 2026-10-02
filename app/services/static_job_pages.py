"""Static, crawlable per-job pages at /job/{url_id} — one plain-HTML page per
live, US, canonical job, with the full description and a schema.org
JobPosting block (what Google for Jobs reads). The day/sector/country pages
in app.services.static_pages link here. The SPA keeps its own /jobs/{url_id}
route; these pages send visitors there to sign in, apply, save or tailor.

Driven by generate_static_job_pages.py on the same schedule as the day pages,
incrementally: a manifest (_meta/job-pages.json) records every live page
and the posting's scanned_at when it was rendered, so each run only

  - renders jobs that are new, or whose scanned_at moved (a rescan),
  - replaces jobs that stopped qualifying (closed, flagged, now a duplicate,
    rescanned into a failure, past MAX_AGE_DAYS, deleted) with a noindex
    "no longer available" page. Deleting the object instead would fall through
    to CloudFront's SPA fallback: a soft 404 that redirects.

and rewrites the sitemap. Bump PAGE_VERSION when the template or rendering
changes: the next runs re-render every page, MAX_RENDERS_PER_RUN at a time
(--full does it all at once).
"""

from __future__ import annotations

import html
import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from markdown_it import MarkdownIt
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import EmploymentType, JobSector, ScanStatus, WorkplaceType
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services.job_locations import split_locations
from app.services.static_pages import (
    _TEMPLATE_ENV,
    COUNTRY_ISO2_BY_SLUG,
    COUNTRY_NAMES,
    SECTOR_DISPLAY_NAMES,
    SECTOR_SLUGS,
    JobRow,
    read_json,
    upload_html,
    upload_xml,
    write_json,
)

logger = logging.getLogger("app.static_job_pages")

PAGE_VERSION = 1
MANIFEST_KEY = "_meta/job-pages.json"
SITEMAP_INDEX_KEY = "sitemap-job-pages.xml"
SITEMAP_SHARD_SIZE = 45_000  # under the sitemap protocol's 50,000-URL cap
# Nothing re-checks a user-submitted URL unless someone rescans it, so this
# is the backstop that keeps a long-dead job from staying indexed forever.
MAX_AGE_DAYS = 60
# Backstop for a failed CloudFront invalidation (see
# generate_static_job_pages.py), and keeps browsers from holding stale copies.
CACHE_CONTROL = "public, max-age=3600"
RENDER_BATCH_SIZE = 500
# Save the manifest every this many batches, so a run that dies partway
# (the backfill, mostly) keeps what it already published.
CHECKPOINT_EVERY_BATCHES = 10
# Renders per scheduled run, so a big backlog (the first run, a source
# rescan, a PAGE_VERSION bump) catches up over a few runs instead of
# blowing the function's 540s timeout. Whatever's left over stays out of
# the manifest and is simply picked up next run. --full (the backfill job,
# with an hour to work) is uncapped.
MAX_RENDERS_PER_RUN = 8_000
UPLOAD_WORKERS = 16
COUNTRY_SLUG = "us"

_EMPLOYMENT_TYPES = {
    EmploymentType.FULL_TIME: "FULL_TIME",
    EmploymentType.PART_TIME: "PART_TIME",
    EmploymentType.CONTRACT: "CONTRACTOR",
    EmploymentType.INTERNSHIP: "INTERN",
    EmploymentType.TEMPORARY: "TEMPORARY",
}
_WORKPLACE_LABELS = {WorkplaceType.REMOTE: "Remote", WorkplaceType.HYBRID: "Hybrid", WorkplaceType.ONSITE: "On-site"}
_EMPLOYMENT_LABELS = {
    EmploymentType.FULL_TIME: "Full-time",
    EmploymentType.PART_TIME: "Part-time",
    EmploymentType.CONTRACT: "Contract",
    EmploymentType.INTERNSHIP: "Internship",
    EmploymentType.TEMPORARY: "Temporary",
}


def _build_markdown() -> MarkdownIt:
    # html=False: descriptions are scraped from employers' pages, so any raw
    # HTML in them is escaped, never passed through. markdown-it's own link
    # validation already drops javascript:/data: URLs.
    md = MarkdownIt("commonmark", {"html": False})

    def link_open(self, tokens, idx, options, env):
        tokens[idx].attrSet("rel", "nofollow ugc")
        return self.renderToken(tokens, idx, options, env)

    md.add_render_rule("link_open", link_open)
    return md


_MARKDOWN = _build_markdown()


def render_job_description_html(description: str | None) -> str:
    return _MARKDOWN.render(description) if description else ""


def _plain_text(description_html: str, limit: int = 155) -> str:
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", description_html)).split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(".,;:") + "…"


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


def _eligible_filter(now: datetime):
    """Same base filter as static_pages.jobs_for_sector_day (and so as a
    normal /jobs search) — successfully scanned, titled, canonical, unflagged,
    US — plus still open and inside MAX_AGE_DAYS."""
    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    return and_(
        JobPosting.extraction_status == ScanStatus.SUCCESS,
        JobPosting.title.is_not(None),
        JobPosting.primary_posting_id.is_(None),
        JobPosting.country == COUNTRY_ISO2_BY_SLUG[COUNTRY_SLUG],
        JobPostingUrl.flagged_at.is_(None),
        JobPostingUrl.closed_at.is_(None),
        or_(
            JobPosting.posted_at >= cutoff.date(),
            and_(JobPosting.posted_at.is_(None), JobPostingUrl.created_at >= cutoff),
        ),
    )


def eligible_job_ids(db: Session, now: datetime) -> dict[str, str]:
    """{url_id: version} for every job that should have a live page. The
    version is the posting's scanned_at, which only moves on a (successful)
    rescan — so an unchanged job is skipped without loading its description."""
    stmt = (
        select(JobPostingUrl.id, JobPosting.scanned_at, JobPostingUrl.created_at)
        .join(JobPosting, JobPosting.url_id == JobPostingUrl.id)
        .where(_eligible_filter(now))
    )
    ids: dict[str, str] = {}
    for url_id, scanned_at, created_at in db.execute(stmt):
        version = _iso(scanned_at or created_at)
        key = str(url_id)
        # A URL with several posting rows: the newest scan wins, same as
        # to_job_detail's latest_posting.
        if key not in ids or version > ids[key]:
            ids[key] = version
    return ids


def _iso(value: datetime) -> str:
    if value.tzinfo is None:  # SQLite (tests) hands back naive datetimes
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


@dataclass
class JobPage:
    url_id: str
    title: str
    company_name: str | None
    domain: str
    location: str | None
    locations: list[str]
    workplace_type: str
    employment_type: str
    sector: str
    description: str | None
    posted_at: date | None
    found_at: datetime
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None

    @property
    def company_display(self) -> str:
        return self.company_name or self.domain

    @property
    def salary_display(self) -> str | None:
        return JobRow(
            url_id=self.url_id, title=self.title, company_name=None, location=None, scanned_at_local=self.found_at,
            salary_min=self.salary_min, salary_max=self.salary_max, salary_currency=self.salary_currency,
        ).salary_display

    @property
    def date_posted(self) -> date:
        return self.posted_at or self.found_at.date()


def load_job_pages(db: Session, url_ids: Iterable[str]) -> dict[str, JobPage]:
    ids = list(url_ids)
    if not ids:
        return {}
    # Just the columns a page uses: whole rows would drag raw_source (the
    # scanned page's HTML) and extracted_fields along for every job, a lot
    # of bytes off a small database for nothing.
    stmt = (
        select(
            JobPostingUrl.id, JobPostingUrl.domain, JobPostingUrl.created_at,
            JobPosting.title, JobPosting.company_name, JobPosting.location, JobPosting.locations,
            JobPosting.workplace_type, JobPosting.employment_type, JobPosting.sector, JobPosting.description,
            JobPosting.posted_at, JobPosting.salary_min, JobPosting.salary_max, JobPosting.salary_currency,
        )
        .join(JobPosting, JobPosting.url_id == JobPostingUrl.id)
        .where(JobPostingUrl.id.in_([uuid.UUID(i) for i in ids]))
        .order_by(JobPosting.scanned_at.asc())  # newest last, so it wins below
    )
    pages: dict[str, JobPage] = {}
    for row in _execute_with_retry(db, stmt):
        if row.title is None:
            continue
        pages[str(row.id)] = JobPage(
            url_id=str(row.id),
            title=row.title,
            company_name=row.company_name,
            domain=row.domain,
            location=row.location,
            locations=list(row.locations or []) or split_locations(row.location),
            workplace_type=row.workplace_type,
            employment_type=row.employment_type,
            sector=row.sector,
            description=row.description,
            posted_at=row.posted_at,
            found_at=row.created_at,
            salary_min=row.salary_min,
            salary_max=row.salary_max,
            salary_currency=row.salary_currency,
        )
    return pages


def _execute_with_retry(db: Session, stmt):
    """One retry on a dropped connection. Cloud SQL closes connections when
    a crawl burst runs it out of slots, and a long backfill shouldn't die
    of one transient drop (the session hands out a fresh connection after
    the rollback)."""
    try:
        return db.execute(stmt).all()
    except OperationalError:
        logger.warning("Database connection dropped while loading job pages; retrying once.")
        db.rollback()
        return db.execute(stmt).all()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _job_ld(job: JobPage, description_html: str) -> str:
    """schema.org JobPosting, pre-escaped for a <script> block the same way
    static_pages._build_job_ld_json does it ("</" can't close the tag)."""
    base = settings.seo_pages_base_url
    country_iso2 = COUNTRY_ISO2_BY_SLUG[COUNTRY_SLUG]
    item: dict = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": job.title,
        "description": description_html or html.escape(job.title),
        "datePosted": job.date_posted.isoformat(),
        "hiringOrganization": {"@type": "Organization", "name": job.company_display},
        "identifier": {"@type": "PropertyValue", "name": "Yabot Jobs", "value": job.url_id},
        "directApply": False,
        "url": f"{base}/job/{job.url_id}",
    }
    places = []
    for loc in job.locations[:10]:
        parts = [p.strip() for p in loc.split(",") if p.strip()]
        address: dict = {"@type": "PostalAddress", "addressCountry": country_iso2}
        if parts:
            address["addressLocality"] = parts[0]
        if len(parts) > 1:
            address["addressRegion"] = parts[1]
        places.append({"@type": "Place", "address": address})
    if job.workplace_type == WorkplaceType.REMOTE:
        item["jobLocationType"] = "TELECOMMUTE"
        item["applicantLocationRequirements"] = {"@type": "Country", "name": COUNTRY_NAMES[COUNTRY_SLUG]}
    if places:
        item["jobLocation"] = places if len(places) > 1 else places[0]
    if job.employment_type in _EMPLOYMENT_TYPES:
        item["employmentType"] = _EMPLOYMENT_TYPES[job.employment_type]
    if job.salary_min is not None or job.salary_max is not None:
        value: dict = {"@type": "QuantitativeValue"}
        if job.salary_min is not None:
            value["minValue"] = job.salary_min
        if job.salary_max is not None:
            value["maxValue"] = job.salary_max
        # The scan doesn't record a pay period; anything under 1,000 is an
        # hourly rate in practice, anything above an annual salary.
        value["unitText"] = "HOUR" if max(v for v in (job.salary_min, job.salary_max) if v is not None) < 1000 else "YEAR"
        item["baseSalary"] = {"@type": "MonetaryAmount", "currency": job.salary_currency or "USD", "value": value}
    return json.dumps(item).replace("</", "<\\/")


def _sector_link(sector: str) -> tuple[str, str] | None:
    try:
        sector_enum = JobSector(sector)
    except ValueError:
        return None
    if sector_enum not in SECTOR_SLUGS:
        return None
    return SECTOR_SLUGS[sector_enum], SECTOR_DISPLAY_NAMES[sector_enum]


def render_job_page(job: JobPage) -> str:
    description_html = render_job_description_html(job.description)
    title = f"{job.title} at {job.company_display}" + (f" – {job.location}" if job.location else "")
    meta_bits = [job.company_display]
    if job.location:
        meta_bits.append(job.location)
    meta_description = _plain_text(description_html) or f"{job.title} at {job.company_display}."
    tags = [
        label
        for label in (
            _WORKPLACE_LABELS.get(job.workplace_type),
            _EMPLOYMENT_LABELS.get(job.employment_type),
            job.salary_display,
        )
        if label
    ]
    return _TEMPLATE_ENV.get_template("job.html.jinja").render(
        job=job,
        page_title=title,
        meta_description=meta_description,
        meta_line=" · ".join(meta_bits),
        posted_display=job.date_posted.strftime("%b %-d, %Y"),
        tags=tags,
        description_html=description_html,
        job_ld_json=_job_ld(job, description_html),
        sector_link=_sector_link(job.sector),
        country_slug=COUNTRY_SLUG,
        country_name=COUNTRY_NAMES[COUNTRY_SLUG],
        base_url=settings.seo_pages_base_url,
    )


def render_job_gone(job: JobPage | None) -> str:
    return _TEMPLATE_ENV.get_template("job_gone.html.jinja").render(
        job=job,
        sector_link=_sector_link(job.sector) if job else None,
        country_slug=COUNTRY_SLUG,
        country_name=COUNTRY_NAMES[COUNTRY_SLUG],
        base_url=settings.seo_pages_base_url,
    )


def build_job_sitemaps(pages: dict[str, str]) -> dict[str, str]:
    """{key: xml} — numbered shards of SITEMAP_SHARD_SIZE URLs plus the
    sitemap index (SITEMAP_INDEX_KEY) that robots.txt points at. `pages` is
    the manifest's {url_id: "<PAGE_VERSION>|<scanned_at>"}; a page's lastmod
    is its scan date."""
    base = settings.seo_pages_base_url
    ordered = sorted(pages.items())
    shards = [ordered[i : i + SITEMAP_SHARD_SIZE] for i in range(0, len(ordered), SITEMAP_SHARD_SIZE)] or [[]]
    out: dict[str, str] = {}
    index_entries = []
    for n, shard in enumerate(shards, start=1):
        key = f"sitemap-job-pages-{n}.xml"
        body = "\n".join(
            f"  <url><loc>{base}/job/{url_id}</loc><lastmod>{entry.rsplit('|', 1)[-1][:10]}</lastmod></url>"
            for url_id, entry in shard
        )
        out[key] = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{body}\n"
            "</urlset>\n"
        )
        index_entries.append(f"  <sitemap><loc>{base}/{key}</loc></sitemap>")
    out[SITEMAP_INDEX_KEY] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(index_entries)
        + "\n</sitemapindex>\n"
    )
    return out


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def read_job_manifest() -> dict:
    manifest = read_json(MANIFEST_KEY)
    if not manifest or not isinstance(manifest.get("pages"), dict):
        return {"pages": {}}
    return manifest


@dataclass
class JobPagesResult:
    published: int = 0  # new pages
    updated: int = 0  # re-rendered: rescanned, or a full/version re-render
    removed: int = 0  # replaced with the "no longer available" page
    unchanged: int = 0
    deferred: int = 0  # over this run's render cap; picked up next run
    # CloudFront paths whose cached copy is now stale (new keys need none).
    touched_paths: list[str] = field(default_factory=list)


def generate_job_pages(
    db: Session,
    *,
    dry_run: bool = False,
    full: bool = False,
    now: datetime | None = None,
    max_renders: int | None = MAX_RENDERS_PER_RUN,
) -> JobPagesResult:
    now = now or datetime.now(timezone.utc)
    previous: dict[str, str] = {} if dry_run else read_job_manifest()["pages"]
    # Each manifest entry is "<PAGE_VERSION>|<scanned_at>", so a rescan or a
    # PAGE_VERSION bump both show up as a changed entry, and progress
    # through a capped backlog is recorded page by page.
    current = {i: f"{PAGE_VERSION}|{scanned}" for i, scanned in eligible_job_ids(db, now).items()}
    if full:
        max_renders = None

    # Already-published pages first (rescanned or stale-versioned: a crawler
    # may be reading the outdated copy right now), then brand-new ones.
    changed_ids = [i for i in current if i in previous and (full or previous[i] != current[i])]
    new_ids = [i for i in current if i not in previous]
    removed_ids = [i for i in previous if i not in current]
    deferred = 0
    if max_renders is not None and len(changed_ids) + len(new_ids) > max_renders:
        deferred = len(changed_ids) + len(new_ids) - max_renders
        changed_ids = changed_ids[:max_renders]
        new_ids = new_ids[: max_renders - len(changed_ids)]
    result = JobPagesResult(
        published=len(new_ids),
        updated=len(changed_ids),
        removed=len(removed_ids),
        unchanged=len(current) - len(new_ids) - len(changed_ids) - deferred,
        deferred=deferred,
    )
    logger.info(
        "Job pages: %d new, %d to re-render, %d to remove, %d unchanged, %d deferred to the next run%s.",
        result.published, result.updated, result.removed, result.unchanged, deferred, " (dry run)" if dry_run else "",
    )
    if dry_run:
        return result

    # What's live in the bucket, kept current as batches land and saved
    # every CHECKPOINT_EVERY_BATCHES. It starts as the old manifest, gains
    # each rendered page's new entry, and drops each page replaced by the
    # "no longer available" one. What's left untouched is the unchanged
    # pages plus deferred changed ones (old entry, so retried next run);
    # deferred new jobs are never added, so they stay out of the sitemap.
    pages = dict(previous)
    batches_done = 0

    def checkpoint(force: bool = False) -> None:
        if force or batches_done % CHECKPOINT_EVERY_BATCHES == 0:
            write_json(MANIFEST_KEY, {"pages": pages})

    with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
        to_render = changed_ids + new_ids
        for start in range(0, len(to_render), RENDER_BATCH_SIZE):
            batch = to_render[start : start + RENDER_BATCH_SIZE]
            loaded = load_job_pages(db, batch)
            uploads = [(f"job/{i}", render_job_page(loaded[i])) for i in batch if i in loaded]
            list(pool.map(lambda kv: upload_html(kv[0], kv[1], CACHE_CONTROL), uploads))
            pages.update((i, current[i]) for i in batch if i in loaded)
            batches_done += 1
            checkpoint()
        for start in range(0, len(removed_ids), RENDER_BATCH_SIZE):
            batch = removed_ids[start : start + RENDER_BATCH_SIZE]
            loaded = load_job_pages(db, batch)
            uploads = [(f"job/{i}", render_job_gone(loaded.get(i))) for i in batch]
            list(pool.map(lambda kv: upload_html(kv[0], kv[1], CACHE_CONTROL), uploads))
            for i in batch:
                pages.pop(i, None)
            batches_done += 1
            checkpoint()

    checkpoint(force=True)
    for key, xml in build_job_sitemaps(pages).items():
        upload_xml(key, xml)
        result.touched_paths.append(f"/{key}")
    result.touched_paths.extend(f"/job/{i}" for i in changed_ids + removed_ids)
    return result
