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
changes so the next run re-renders every page (same as --full).
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
    stmt = (
        select(JobPostingUrl, JobPosting)
        .join(JobPosting, JobPosting.url_id == JobPostingUrl.id)
        .where(JobPostingUrl.id.in_([uuid.UUID(i) for i in ids]))
        .order_by(JobPosting.scanned_at.asc())  # newest last, so it wins below
    )
    pages: dict[str, JobPage] = {}
    for url_row, posting in db.execute(stmt):
        if posting.title is None:
            continue
        pages[str(url_row.id)] = JobPage(
            url_id=str(url_row.id),
            title=posting.title,
            company_name=posting.company_name,
            domain=url_row.domain,
            location=posting.location,
            locations=list(posting.locations or []) or split_locations(posting.location),
            workplace_type=posting.workplace_type,
            employment_type=posting.employment_type,
            sector=posting.sector,
            description=posting.description,
            posted_at=posting.posted_at,
            found_at=url_row.created_at,
            salary_min=posting.salary_min,
            salary_max=posting.salary_max,
            salary_currency=posting.salary_currency,
        )
    return pages


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
    the manifest's {url_id: version}; a page's lastmod is its version's date."""
    base = settings.seo_pages_base_url
    ordered = sorted(pages.items())
    shards = [ordered[i : i + SITEMAP_SHARD_SIZE] for i in range(0, len(ordered), SITEMAP_SHARD_SIZE)] or [[]]
    out: dict[str, str] = {}
    index_entries = []
    for n, shard in enumerate(shards, start=1):
        key = f"sitemap-job-pages-{n}.xml"
        body = "\n".join(
            f"  <url><loc>{base}/job/{url_id}</loc><lastmod>{version[:10]}</lastmod></url>" for url_id, version in shard
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
        return {"version": None, "pages": {}}
    return manifest


@dataclass
class JobPagesResult:
    published: int = 0  # new pages
    updated: int = 0  # re-rendered: rescanned, or a full/version re-render
    removed: int = 0  # replaced with the "no longer available" page
    unchanged: int = 0
    # CloudFront paths whose cached copy is now stale (new keys need none).
    touched_paths: list[str] = field(default_factory=list)


def generate_job_pages(
    db: Session, *, dry_run: bool = False, full: bool = False, now: datetime | None = None
) -> JobPagesResult:
    now = now or datetime.now(timezone.utc)
    manifest = {"version": None, "pages": {}} if dry_run else read_job_manifest()
    previous: dict[str, str] = manifest["pages"]
    eligible = eligible_job_ids(db, now)
    rerender_all = full or manifest["version"] != PAGE_VERSION

    new_ids = [i for i in eligible if i not in previous]
    changed_ids = [i for i in eligible if i in previous and (rerender_all or previous[i] != eligible[i])]
    removed_ids = [i for i in previous if i not in eligible]
    result = JobPagesResult(
        published=len(new_ids),
        updated=len(changed_ids),
        removed=len(removed_ids),
        unchanged=len(eligible) - len(new_ids) - len(changed_ids),
    )
    logger.info(
        "Job pages: %d new, %d to re-render, %d to remove, %d unchanged%s.",
        result.published, result.updated, result.removed, result.unchanged, " (dry run)" if dry_run else "",
    )
    if dry_run:
        return result

    with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
        to_render = new_ids + changed_ids
        for start in range(0, len(to_render), RENDER_BATCH_SIZE):
            batch = to_render[start : start + RENDER_BATCH_SIZE]
            pages = load_job_pages(db, batch)
            uploads = [(f"job/{i}", render_job_page(pages[i])) for i in batch if i in pages]
            list(pool.map(lambda kv: upload_html(kv[0], kv[1], CACHE_CONTROL), uploads))
        for start in range(0, len(removed_ids), RENDER_BATCH_SIZE):
            batch = removed_ids[start : start + RENDER_BATCH_SIZE]
            pages = load_job_pages(db, batch)
            uploads = [(f"job/{i}", render_job_gone(pages.get(i))) for i in batch]
            list(pool.map(lambda kv: upload_html(kv[0], kv[1], CACHE_CONTROL), uploads))

    # Written only once every page is up: a run that dies halfway leaves the
    # old manifest, and the next run simply redoes the same work.
    write_json(MANIFEST_KEY, {"version": PAGE_VERSION, "pages": eligible})
    for key, xml in build_job_sitemaps(eligible).items():
        upload_xml(key, xml)
        result.touched_paths.append(f"/{key}")
    result.touched_paths.extend(f"/job/{i}" for i in changed_ids + removed_ids)
    return result
