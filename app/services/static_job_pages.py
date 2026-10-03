"""Static, crawlable per-job pages — one plain-HTML page per live, US,
canonical job, with the full description and a schema.org JobPosting block
(what Google for Jobs reads), plus links to related jobs and to the company
and location hubs (app.services.static_hub_pages). The day/sector/country
pages in app.services.static_pages link here. The SPA keeps its own
/jobs/{url_id} route; these pages send visitors there to sign in, apply,
save or tailor.

URLs. A job published since 2026-10-03 lives under the day page that lists
it: /jobs/us/<sector>/<YYYY-MM-DD>/<title>-<place>-<url_id> (see
new_job_path). Jobs published before that keep /job/<url_id>. Either way a
page's path is decided once, when it's first published, and stored in the
manifest — later renders reuse it even if a rescan changes the title, sector
or posted date, so a URL never moves and nothing ever needs a redirect.

Driven by generate_static_job_pages.py on the same schedule as the day pages,
incrementally: a manifest (_meta/job-pages.json) records every live page —
the posting's scanned_at when it was rendered, its path and when it was
rendered — so each run only

  - renders jobs that are new, or whose scanned_at moved (a rescan),
  - replaces jobs that stopped qualifying (closed, flagged, now a duplicate,
    rescanned into a failure, past MAX_AGE_DAYS, deleted) with a noindex
    "no longer available" page. Deleting the object instead would fall through
    to CloudFront's SPA fallback: a soft 404 that redirects.
  - spends any render capacity left over on re-rendering the pages rendered
    longest ago, so their related-job links don't go stale forever,

then rebuilds the hubs and the sitemaps. Bump PAGE_VERSION when the template
or rendering changes: the next runs re-render every page, MAX_RENDERS_PER_RUN
at a time (--full does it all at once).
"""

from __future__ import annotations

import html
import json
import logging
import re
import unicodedata
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
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
from app.services import geo
from app.services.company_logos import logo_url_for
from app.services.job_locations import split_locations
from app.services.static_pages import (
    _TEMPLATE_ENV,
    COUNTRY_ISO2_BY_SLUG,
    COUNTRY_NAMES,
    DAY_BOUNDARY_TZ,
    SECTOR_DISPLAY_NAMES,
    SECTOR_SLUGS,
    JobRow,
    read_json,
    upload_html,
    upload_xml,
    write_json,
)

logger = logging.getLogger("app.static_job_pages")

PAGE_VERSION = 2
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
# Leftover render capacity re-renders pages last rendered longer ago than
# this, so their related-job links don't go stale for good — without
# re-rendering every page on every run.
REFRESH_AFTER_DAYS = 7
COUNTRY_SLUG = "us"
# A job page's "More jobs at …" and "Similar jobs" lists.
RELATED_LIMIT = 6
# The segment for jobs with no (or an unknown) sector. There are no day or
# sector pages under it, deliberately — see static_pages._SECTOR_INFO.
OTHER_SECTOR_SLUG = "other"
_TITLE_SLUG_MAX = 60

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


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

# A paragraph that is nothing but a short label ("About the Role",
# "Who We Are:") — what employers use as section headings in plain text.
_HEADING_LABEL_MAX_WORDS = 8


def _promote_label_paragraphs(state) -> None:
    """Turn a paragraph that is only bold text ("**About the Role**"), or a
    short line ending in a colon ("Who We Are:"), into an h3, so the
    description gets real heading structure instead of one block of text."""
    tokens = state.tokens
    for i in range(len(tokens) - 2):
        if tokens[i].type != "paragraph_open" or tokens[i + 2].type != "paragraph_close":
            continue
        inline = tokens[i + 1]
        children = [c for c in (inline.children or []) if not (c.type == "text" and not c.content.strip())]
        bold_only = (
            len(children) == 3
            and children[0].type == "strong_open"
            and children[1].type == "text"
            and children[2].type == "strong_close"
        )
        text = inline.content.strip().strip("*_").strip()
        colon_label = (
            len(children) == 1
            and children[0].type == "text"
            and text.endswith(":")
            and len(text.split()) <= _HEADING_LABEL_MAX_WORDS
        )
        if not (bold_only or colon_label) or not text or len(text.split()) > _HEADING_LABEL_MAX_WORDS:
            continue
        for token, kind in ((tokens[i], "heading_open"), (tokens[i + 2], "heading_close")):
            token.type, token.tag = kind, "h3"
        inline.children = [c for c in inline.children if c.type == "text"] if colon_label else [children[1]]


def _build_markdown() -> MarkdownIt:
    # html=False: descriptions are scraped from employers' pages, so any raw
    # HTML in them is escaped, never passed through. markdown-it's own link
    # validation already drops javascript:/data: URLs.
    md = MarkdownIt("commonmark", {"html": False})

    def link_open(self, tokens, idx, options, env):
        tokens[idx].attrSet("rel", "nofollow ugc")
        return self.renderToken(tokens, idx, options, env)

    md.add_render_rule("link_open", link_open)
    md.core.ruler.push("promote_label_paragraphs", _promote_label_paragraphs)
    return md


_MARKDOWN = _build_markdown()


def render_job_description_html(description: str | None) -> str:
    return _MARKDOWN.render(description) if description else ""


# ---------------------------------------------------------------------------
# Slugs and paths
# ---------------------------------------------------------------------------


def slugify(text: str | None, max_len: int | None = None) -> str:
    """Lowercase ASCII, words joined by hyphens; cut at a word boundary when
    longer than max_len."""
    if not text:
        return ""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    if max_len and len(slug) > max_len:
        cut = slug[: max_len + 1]
        slug = cut.rsplit("-", 1)[0] if "-" in cut else slug[:max_len]
    return slug


def sector_slug(sector: str | None) -> str:
    link = _sector_link(sector)
    return link[0] if link else OTHER_SECTOR_SLUG


@lru_cache(maxsize=200_000)
def resolve_first_location(location: str | None):
    """geo.resolve_entry() of a posting's first location, cached by the raw
    location string — many jobs share one ("San Francisco, CA"), and hubs
    resolve every live job's on every run."""
    first = (split_locations(location) or [None])[0]
    if not first:
        return None
    try:
        return geo.resolve_entry(first)
    except Exception:  # a geo bug must never block publishing
        logger.exception("Couldn't resolve location %r.", first)
        return None


def place_slug(location: str | None, workplace_type: str | None) -> str:
    """The place part of a new job URL, from the job's first location:
    its city ("cary-nc"), else its metro, else its state, else "remote" for
    a remote job, else nothing."""
    resolution = resolve_first_location(location)
    if resolution is not None:
        if resolution.place is not None:
            return slugify(f"{resolution.place.name} {resolution.place.state}")
        if resolution.metro is not None:
            return resolution.metro.slug
        if resolution.state is not None:
            return resolution.state.slug
        if resolution.workplace == "remote":
            return "remote"
    return "remote" if workplace_type == WorkplaceType.REMOTE else ""


def job_day(posted_at: date | None, found_at: datetime) -> date:
    """The day page a job is listed on — same rule as
    static_pages.jobs_for_sector_day: the employer's posted date, else the
    Pacific day we first saw it."""
    if posted_at is not None:
        return posted_at
    if found_at.tzinfo is None:
        found_at = found_at.replace(tzinfo=timezone.utc)
    return found_at.astimezone(DAY_BOUNDARY_TZ).date()


def new_job_path(job: "LinkJob") -> str:
    """/jobs/us/<sector>/<day>/<title>-<place>-<url_id>, without the leading
    slash. Only for a job being published for the first time — see the
    module docstring on why a path never changes afterwards."""
    last = "-".join(p for p in (slugify(job.title, _TITLE_SLUG_MAX), job.place_slug, job.url_id) if p)
    return f"jobs/{COUNTRY_SLUG}/{sector_slug(job.sector)}/{job.day.isoformat()}/{last}"


def legacy_job_path(url_id: str) -> str:
    """Where every page published before 2026-10-03 lives, and stays."""
    return f"job/{url_id}"


# ---------------------------------------------------------------------------
# Manifest entries: "<PAGE_VERSION>|<scanned_at>|<path>|<rendered_at>"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestEntry:
    version_key: str  # "<PAGE_VERSION>|<scanned_at>" — a change means re-render
    path: str
    rendered_at: str  # ISO; "" for entries written before it was recorded

    def dump(self) -> str:
        return f"{self.version_key}|{self.path}|{self.rendered_at}"


def parse_entry(url_id: str, raw: str) -> ManifestEntry:
    """Tolerates the older two-part "<version>|<scanned_at>" entries, whose
    pages all live at /job/<url_id>."""
    parts = raw.split("|")
    return ManifestEntry(
        version_key="|".join(parts[:2]),
        path=parts[2] if len(parts) > 2 and parts[2] else legacy_job_path(url_id),
        rendered_at=parts[3] if len(parts) > 3 else "",
    )


def read_job_manifest() -> dict:
    manifest = read_json(MANIFEST_KEY)
    if not manifest or not isinstance(manifest.get("pages"), dict):
        return {"pages": {}}
    return manifest


def read_job_paths() -> dict[str, str]:
    """{url_id: path} for every live page — what the day pages link to when
    this run's job pass didn't produce a fresh map (it failed)."""
    return {i: parse_entry(i, raw).path for i, raw in read_job_manifest()["pages"].items()}


# ---------------------------------------------------------------------------
# Query: one lightweight row per eligible job
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


@dataclass(slots=True)
class LinkJob:
    """What's needed to link to a job, group it into hubs and decide whether
    its page needs rendering — no description, so ~280k of these fit in a
    run's memory."""

    url_id: str
    title: str
    company_name: str | None
    domain: str
    company_key: str | None
    title_key: str | None
    sector: str
    metros: tuple[str, ...]
    location: str | None
    workplace_type: str
    posted_at: date | None
    found_at: datetime
    scanned: str  # ISO; the page's version
    path: str = ""
    _place: str | None = None

    @property
    def company_display(self) -> str:
        return self.company_name or self.domain

    @property
    def day(self) -> date:
        return job_day(self.posted_at, self.found_at)

    @property
    def place_slug(self) -> str:
        if self._place is None:
            self._place = place_slug(self.location, self.workplace_type)
        return self._place

    @property
    def city(self) -> str | None:
        """The first location's city, when geo matched one ("Cary", whether
        written "Cary, NC" or "USA, NC, Cary")."""
        resolution = resolve_first_location(self.location)
        return resolution.place.name if resolution is not None and resolution.place is not None else None


def load_link_index(db: Session, now: datetime) -> dict[str, LinkJob]:
    """Every job that should have a live page, keyed by url_id."""
    stmt = (
        select(
            JobPostingUrl.id, JobPostingUrl.domain, JobPostingUrl.created_at,
            JobPosting.title, JobPosting.company_name, JobPosting.company_key, JobPosting.title_key,
            JobPosting.sector, JobPosting.metros, JobPosting.location, JobPosting.workplace_type,
            JobPosting.posted_at, JobPosting.scanned_at, JobPosting.company_logo_key,
        )
        .join(JobPosting, JobPosting.url_id == JobPostingUrl.id)
        .where(_eligible_filter(now))
    )
    index: dict[str, LinkJob] = {}
    for row in _execute_with_retry(db, stmt):
        key = str(row.id)
        scanned = _iso(row.scanned_at or row.created_at)
        if row.company_logo_key:
            # The company's logo too, so a newly fetched or changed logo (see
            # app.services.company_logos) re-renders its pages. Appended with
            # "~" (not "|", the manifest's separator), after the date that
            # sitemap lastmod reads from the front.
            scanned = f"{scanned}~{row.company_logo_key.rsplit('-', 1)[-1].removesuffix('.png')}"
        # A URL with several posting rows: the newest scan wins, same as
        # to_job_detail's latest_posting.
        if key in index and index[key].scanned >= scanned:
            continue
        index[key] = LinkJob(
            url_id=key,
            title=row.title,
            company_name=row.company_name,
            domain=row.domain,
            company_key=row.company_key,
            title_key=row.title_key,
            sector=row.sector,
            metros=tuple(row.metros or ()),
            location=row.location,
            workplace_type=row.workplace_type,
            posted_at=row.posted_at,
            found_at=row.created_at,
            scanned=scanned,
        )
    return index


def eligible_job_ids(db: Session, now: datetime) -> dict[str, str]:
    """{url_id: scanned_at} for every job that should have a live page."""
    return {i: job.scanned for i, job in load_link_index(db, now).items()}


def _iso(value: datetime) -> str:
    if value.tzinfo is None:  # SQLite (tests) hands back naive datetimes
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Query: the full rows for the pages being rendered
# ---------------------------------------------------------------------------


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
    valid_through: str | None = None  # the employer's own, from its JSON-LD
    company_domain: str | None = None
    company_logo_key: str | None = None

    @property
    def company_logo_url(self) -> str | None:
        return logo_url_for(self.company_logo_key)

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

    @property
    def day(self) -> date:
        return job_day(self.posted_at, self.found_at)

    @property
    def expires(self) -> date:
        """The employer's validThrough when it gives one (and it's sane),
        else the day this page is retired: MAX_AGE_DAYS after it was posted
        or found, matching _eligible_filter."""
        employer = _parse_date(self.valid_through)
        if employer is not None and employer >= self.date_posted:
            return employer
        return self.date_posted + timedelta(days=MAX_AGE_DAYS)


def _parse_date(value: str | None) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def load_job_pages(db: Session, url_ids: Iterable[str]) -> dict[str, JobPage]:
    ids = list(url_ids)
    if not ids:
        return {}
    # Just the columns a page uses: whole rows would drag raw_source (the
    # scanned page's HTML) and the rest of extracted_fields along for every
    # job, a lot of bytes off a small database for nothing.
    stmt = (
        select(
            JobPostingUrl.id, JobPostingUrl.domain, JobPostingUrl.created_at,
            JobPosting.title, JobPosting.company_name, JobPosting.location, JobPosting.locations,
            JobPosting.workplace_type, JobPosting.employment_type, JobPosting.sector, JobPosting.description,
            JobPosting.posted_at, JobPosting.salary_min, JobPosting.salary_max, JobPosting.salary_currency,
            JobPosting.extracted_fields["validThrough"].as_string().label("valid_through"),
            JobPosting.company_domain, JobPosting.company_logo_key,
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
            valid_through=row.valid_through,
            company_domain=row.company_domain,
            company_logo_key=row.company_logo_key,
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
# Related jobs
# ---------------------------------------------------------------------------


class RelatedIndex:
    """Groups of live jobs for a page's "More jobs at …" and "Similar jobs"
    lists — built once per run in memory rather than two queries per page.
    Same rules as app.services.jobs.find_similar_job_urls: same company, or
    the exact same normalized title (title_key) at another company; topped
    up from the same sector in the same metro."""

    def __init__(self, live: Iterable[LinkJob]):
        def newest_first(groups: dict) -> dict:
            return {k: sorted(v, key=lambda j: j.found_at, reverse=True) for k, v in groups.items()}

        by_company: dict[str, list[LinkJob]] = defaultdict(list)
        by_title: dict[str, list[LinkJob]] = defaultdict(list)
        by_sector_metro: dict[tuple[str, str], list[LinkJob]] = defaultdict(list)
        for job in live:
            if job.company_key:
                by_company[job.company_key].append(job)
            if job.title_key:
                by_title[job.title_key].append(job)
            for code in job.metros:
                by_sector_metro[(job.sector, code)].append(job)
        self.by_company = newest_first(by_company)
        self.by_title = newest_first(by_title)
        self.by_sector_metro = newest_first(by_sector_metro)

    def same_company(self, job: LinkJob, limit: int = RELATED_LIMIT) -> list[LinkJob]:
        if not job.company_key:
            return []
        return [j for j in self.by_company.get(job.company_key, ()) if j.url_id != job.url_id][:limit]

    def similar(self, job: LinkJob, exclude: Iterable[LinkJob] = (), limit: int = RELATED_LIMIT) -> list[LinkJob]:
        seen = {job.url_id} | {j.url_id for j in exclude}
        picks: list[LinkJob] = []

        def take(candidates):
            for candidate in candidates:
                if len(picks) >= limit:
                    return
                if candidate.url_id in seen or (job.company_key and candidate.company_key == job.company_key):
                    continue
                seen.add(candidate.url_id)
                picks.append(candidate)

        if job.title_key:
            take(self.by_title.get(job.title_key, ()))
        for code in job.metros:
            take(self.by_sector_metro.get((job.sector, code), ()))
        return picks


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _hiring_org(job: JobPage) -> dict:
    org: dict = {"@type": "Organization", "name": job.company_display}
    if job.company_domain:
        org["sameAs"] = f"https://{job.company_domain}"
    if job.company_logo_url:
        org["logo"] = job.company_logo_url
    return org


def _url(path: str) -> str:
    return f"{settings.seo_pages_base_url}/{path}"


def _sector_link(sector: str | None) -> tuple[str, str] | None:
    try:
        sector_enum = JobSector(sector)
    except ValueError:
        return None
    if sector_enum not in SECTOR_SLUGS:
        return None
    return SECTOR_SLUGS[sector_enum], SECTOR_DISPLAY_NAMES[sector_enum]


def breadcrumb_trail(job: JobPage, path: str) -> list[tuple[str, str | None]]:
    """[(label, url or None for the current page), ...] — the same trail for
    the visible breadcrumb and the BreadcrumbList JSON-LD."""
    base = settings.seo_pages_base_url
    trail: list[tuple[str, str | None]] = [
        ("Home", f"{base}/"),
        ("Jobs", f"{base}/jobs"),
        (COUNTRY_NAMES[COUNTRY_SLUG], f"{base}/jobs/{COUNTRY_SLUG}"),
    ]
    sector = _sector_link(job.sector)
    if sector:
        trail.append((sector[1], f"{base}/jobs/{COUNTRY_SLUG}/{sector[0]}"))
        trail.append((job.day.strftime("%b %-d, %Y"), f"{base}/jobs/{COUNTRY_SLUG}/{sector[0]}/{job.day.isoformat()}"))
    trail.append((job.title, None))
    return trail


def breadcrumb_ld(trail: list[tuple[str, str | None]], current_url: str) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": n, "name": label, "item": url or current_url}
            for n, (label, url) in enumerate(trail, start=1)
        ],
    }


def ld_json(payload: dict | list) -> str:
    """JSON-LD for a <script> block: "</" escaped so a title or description
    containing it can't close the tag early (same as
    static_pages._build_job_ld_json)."""
    return json.dumps(payload).replace("</", "<\\/")


def _job_ld(job: JobPage, description_html: str, page_url: str) -> dict:
    """schema.org JobPosting."""
    country_iso2 = COUNTRY_ISO2_BY_SLUG[COUNTRY_SLUG]
    item: dict = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": job.title,
        "description": description_html or html.escape(job.title),
        "datePosted": job.date_posted.isoformat(),
        "validThrough": job.expires.isoformat(),
        "hiringOrganization": _hiring_org(job),
        "identifier": {"@type": "PropertyValue", "name": "Yabot Jobs", "value": job.url_id},
        "directApply": False,
        "url": page_url,
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
    return item


_BRAND = " | Yabot Jobs"
TITLE_MAX = 60
META_DESCRIPTION_MAX = 160


def page_title(job: JobPage) -> str:
    """At most TITLE_MAX characters, the longest of: title – company |
    brand, title | brand, then the title cut at a word boundary."""
    for candidate in (f"{job.title} – {job.company_display}{_BRAND}", f"{job.title}{_BRAND}"):
        if len(candidate) <= TITLE_MAX:
            return candidate
    room = TITLE_MAX - len(_BRAND) - 1
    return job.title[:room].rsplit(" ", 1)[0].rstrip(" ,;:-–") + "…" + _BRAND


def meta_description(job: JobPage) -> str:
    where = f" in {job.location}" if job.location else ""
    facts = [label for label in (_EMPLOYMENT_LABELS.get(job.employment_type), _WORKPLACE_LABELS.get(job.workplace_type)) if label]
    if job.salary_display:
        facts.append(job.salary_display)
    sentences = [
        f"{job.title} at {job.company_display}{where}.",
        (", ".join(facts) + ".") if facts else "",
        f"Posted {job.date_posted.strftime('%b %-d, %Y')}.",
        "See details and similar jobs on Yabot Jobs.",
    ]
    text = ""
    for sentence in filter(None, sentences):
        candidate = f"{text} {sentence}".strip()
        if len(candidate) > META_DESCRIPTION_MAX:
            break
        text = candidate
    if not text:  # one very long title
        text = sentences[0][: META_DESCRIPTION_MAX - 1].rsplit(" ", 1)[0] + "…"
    return text


def _lead(job: JobPage) -> str:
    where = f" in {job.location}" if job.location else ""
    article = "an" if job.title[:1].lower() in "aeiou" else "a"
    return f"{job.company_display} is hiring {article} {job.title}{where}. Posted {job.date_posted.strftime('%B %-d, %Y')}."


def _glance(job: JobPage) -> list[tuple[str, str]]:
    rows = [
        ("Company", job.company_display),
        ("Location", " · ".join(job.locations[:5]) + (f" (+{len(job.locations) - 5} more)" if len(job.locations) > 5 else "") if job.locations else None),
        ("Pay", job.salary_display),
        ("Workplace", _WORKPLACE_LABELS.get(job.workplace_type)),
        ("Employment", _EMPLOYMENT_LABELS.get(job.employment_type)),
        ("Sector", (_sector_link(job.sector) or (None, None))[1]),
        ("Posted", job.date_posted.strftime("%B %-d, %Y")),
        ("Apply by", job.expires.strftime("%B %-d, %Y") if _parse_date(job.valid_through) else None),
    ]
    return [(label, value) for label, value in rows if value]


@dataclass
class PageLinks:
    """The links a job page carries beyond its own content (all optional)."""

    same_company: list[LinkJob] = field(default_factory=list)
    similar: list[LinkJob] = field(default_factory=list)
    company_hub: str | None = None  # path
    location_hubs: list[tuple[str, str]] = field(default_factory=list)  # (label, path)


def render_job_page(job: JobPage, path: str | None = None, links: PageLinks | None = None) -> str:
    path = path or legacy_job_path(job.url_id)
    links = links or PageLinks()
    page_url = _url(path)
    description_html = render_job_description_html(job.description)
    trail = breadcrumb_trail(job, path)
    return _TEMPLATE_ENV.get_template("job.html.jinja").render(
        job=job,
        page_url=page_url,
        page_title=page_title(job),
        meta_description=meta_description(job),
        lead=_lead(job),
        glance=_glance(job),
        description_html=description_html,
        job_ld_json=ld_json([_job_ld(job, description_html, page_url), breadcrumb_ld(trail, page_url)]),
        trail=trail,
        links=links,
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


def build_sitemaps(prefix: str, urls: list[tuple[str, str | None]]) -> dict[str, str]:
    """{key: xml}: numbered `<prefix>-<n>.xml` shards of (path, lastmod)
    URLs, at most SITEMAP_SHARD_SIZE each."""
    base = settings.seo_pages_base_url
    ordered = sorted(urls)
    shards = [ordered[i : i + SITEMAP_SHARD_SIZE] for i in range(0, len(ordered), SITEMAP_SHARD_SIZE)] or [[]]
    out = {}
    for n, shard in enumerate(shards, start=1):
        body = "\n".join(
            f"  <url><loc>{base}/{path}</loc>" + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "") + "</url>"
            for path, lastmod in shard
        )
        out[f"{prefix}-{n}.xml"] = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{body}\n"
            "</urlset>\n"
        )
    return out


def build_job_sitemaps(pages: dict[str, str], hub_paths: Iterable[str] = ()) -> dict[str, str]:
    """{key: xml} — numbered shards of the job pages (sitemap-job-pages-<n>),
    a shard of hub pages (sitemap-hubs-<n>), and the sitemap index
    (SITEMAP_INDEX_KEY) that robots.txt points at. `pages` is the manifest's
    {url_id: entry}; a page's lastmod is its scan date."""
    job_urls = []
    for url_id, raw in pages.items():
        entry = parse_entry(url_id, raw)
        job_urls.append((entry.path, entry.version_key.rsplit("|", 1)[-1][:10]))
    out = build_sitemaps("sitemap-job-pages", job_urls)
    hubs = sorted(hub_paths)
    if hubs:
        out.update(build_sitemaps("sitemap-hubs", [(path, None) for path in hubs]))
    base = settings.seo_pages_base_url
    index_entries = "\n".join(f"  <sitemap><loc>{base}/{key}</loc></sitemap>" for key in sorted(out))
    out[SITEMAP_INDEX_KEY] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{index_entries}\n"
        "</sitemapindex>\n"
    )
    return out


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


@dataclass
class JobPagesResult:
    published: int = 0  # new pages
    updated: int = 0  # re-rendered: rescanned, or a full/version re-render
    refreshed: int = 0  # re-rendered with leftover capacity so links stay fresh
    removed: int = 0  # replaced with the "no longer available" page
    unchanged: int = 0
    deferred: int = 0  # over this run's render cap; picked up next run
    hubs: int = 0  # hub pages published (static_hub_pages)
    # CloudFront paths whose cached copy is now stale (new keys need none).
    touched_paths: list[str] = field(default_factory=list)
    # {url_id: path} of every live page, for the day pages' links.
    job_paths: dict[str, str] = field(default_factory=dict)
    # Top company/location hub links for the country page (static_hub_pages).
    country_links: dict = field(default_factory=dict)


def generate_job_pages(
    db: Session,
    *,
    dry_run: bool = False,
    full: bool = False,
    now: datetime | None = None,
    max_renders: int | None = MAX_RENDERS_PER_RUN,
) -> JobPagesResult:
    from app.services import static_hub_pages  # imports this module

    now = now or datetime.now(timezone.utc)
    rendered_at = now.isoformat()
    previous = {} if dry_run else {i: parse_entry(i, raw) for i, raw in read_job_manifest()["pages"].items()}
    index = load_link_index(db, now)
    # A rescan (scanned_at moved) or a PAGE_VERSION bump changes the key.
    current = {i: f"{PAGE_VERSION}|{job.scanned}" for i, job in index.items()}
    for i, job in index.items():
        job.path = previous[i].path if i in previous else new_job_path(job)
    if full:
        max_renders = None

    # Already-published pages first (rescanned or stale-versioned: a crawler
    # may be reading the outdated copy right now), then brand-new ones.
    changed_ids = [i for i in current if i in previous and (full or previous[i].version_key != current[i])]
    new_ids = [i for i in current if i not in previous]
    removed_ids = [i for i in previous if i not in current]
    deferred = 0
    if max_renders is not None and len(changed_ids) + len(new_ids) > max_renders:
        deferred = len(changed_ids) + len(new_ids) - max_renders
        changed_ids = changed_ids[:max_renders]
        new_ids = new_ids[: max_renders - len(changed_ids)]
    # Leftover capacity re-renders the pages rendered longest ago (past
    # REFRESH_AFTER_DAYS), so their related-job links catch up over time.
    refresh_ids: list[str] = []
    if max_renders is not None and not deferred:
        room = max_renders - len(changed_ids) - len(new_ids)
        touched = set(changed_ids)
        refresh_before = (now - timedelta(days=REFRESH_AFTER_DAYS)).isoformat()
        stale = sorted(
            (previous[i].rendered_at, i)
            for i in current
            if i in previous and i not in touched and previous[i].rendered_at < refresh_before
        )
        refresh_ids = [i for _, i in stale[:room]]

    # What will be live once this run is done: everything already published
    # plus this run's new pages. Deferred new jobs aren't linked from
    # anywhere until they're published.
    live_ids = (set(previous) & set(current)) | set(new_ids)
    live = [index[i] for i in live_ids]
    hub_plan = static_hub_pages.plan_hubs(live)

    result = JobPagesResult(
        published=len(new_ids),
        updated=len(changed_ids),
        refreshed=len(refresh_ids),
        removed=len(removed_ids),
        unchanged=len(current) - len(new_ids) - len(changed_ids) - len(refresh_ids) - deferred,
        deferred=deferred,
        hubs=len(hub_plan.pages),
        job_paths={i: index[i].path for i in live_ids},
        country_links=hub_plan.country_links(),
    )
    logger.info(
        "Job pages: %d new, %d to re-render, %d to refresh, %d to remove, %d unchanged, %d deferred to the next "
        "run; %d hub page(s)%s.",
        result.published, result.updated, result.refreshed, result.removed, result.unchanged, deferred,
        result.hubs, " (dry run)" if dry_run else "",
    )
    if dry_run:
        return result

    related = RelatedIndex(live)

    def links_for(job: LinkJob) -> PageLinks:
        same_company = related.same_company(job)
        return PageLinks(
            same_company=same_company,
            similar=related.similar(job, exclude=same_company),
            company_hub=hub_plan.company_path(job),
            location_hubs=hub_plan.location_links(job),
        )

    # What's live in the bucket, kept current as batches land and saved
    # every CHECKPOINT_EVERY_BATCHES. It starts as the old manifest, gains
    # each rendered page's new entry, and drops each page replaced by the
    # "no longer available" one. What's left untouched is the unchanged
    # pages plus deferred changed ones (old entry, so retried next run);
    # deferred new jobs are never added, so they stay out of the sitemap.
    pages = {i: entry.dump() for i, entry in previous.items()}
    batches_done = 0

    def checkpoint(force: bool = False) -> None:
        if force or batches_done % CHECKPOINT_EVERY_BATCHES == 0:
            write_json(MANIFEST_KEY, {"pages": pages})

    with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
        to_render = changed_ids + new_ids + refresh_ids
        for start in range(0, len(to_render), RENDER_BATCH_SIZE):
            batch = to_render[start : start + RENDER_BATCH_SIZE]
            loaded = load_job_pages(db, batch)
            uploads = [
                (index[i].path, render_job_page(loaded[i], index[i].path, links_for(index[i])))
                for i in batch
                if i in loaded
            ]
            list(pool.map(lambda kv: upload_html(kv[0], kv[1], CACHE_CONTROL), uploads))
            pages.update(
                (i, ManifestEntry(current[i], index[i].path, rendered_at).dump()) for i in batch if i in loaded
            )
            batches_done += 1
            checkpoint()
        for start in range(0, len(removed_ids), RENDER_BATCH_SIZE):
            batch = removed_ids[start : start + RENDER_BATCH_SIZE]
            loaded = load_job_pages(db, batch)
            uploads = [(previous[i].path, render_job_gone(loaded.get(i))) for i in batch]
            list(pool.map(lambda kv: upload_html(kv[0], kv[1], CACHE_CONTROL), uploads))
            for i in batch:
                pages.pop(i, None)
            batches_done += 1
            checkpoint()

        checkpoint(force=True)
        hub_paths = static_hub_pages.publish_hubs(hub_plan, pool)

    for key, xml in build_job_sitemaps(pages, hub_paths).items():
        upload_xml(key, xml)
        result.touched_paths.append(f"/{key}")
    # Hubs are rewritten every run; they all sit under /jobs/, which
    # collapse_invalidation_paths turns into one wildcard.
    result.touched_paths.extend(f"/{previous[i].path}" for i in changed_ids + refresh_ids + removed_ids)
    result.touched_paths.extend(f"/{path}" for path in hub_paths)
    return result
