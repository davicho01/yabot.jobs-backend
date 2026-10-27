"""Static pages for approved job collections (app.models.job_collection),
published by the static generator beside the hubs (ADR-002, ADR-003):

  /jobs/us/<role>                     e.g. software-engineer
  /jobs/us/<role>/remote              its remote jobs
  /jobs/us/<role>/<metro or state>    e.g. software-engineer/raleigh-nc
  .../page/<n>                        the rest of the list, PER_PAGE a page

Only role collections have pages so far: category and location ones are
what the hubs and sector pages already publish (ADR-004), and salary and
freshness ones come later.

A collection has a page while its status is live (LIVE_STATUSES); it is
indexed only while job_collections.is_indexable — a NOINDEX one keeps its
page with noindex. A page that goes away (archived, back to candidate, or
the flag turned off) gets the hubs' noindex "no current jobs" page, tracked
in _meta/collection-pages.json. Nothing here runs unless
settings.job_collections_enabled.

Paths can't collide with the other pages under /jobs/us/: a role slug is
never a sector slug or one of RESERVED_SEGMENTS (tests check), and
"page" is never a location slug.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import CollectionStatus as S
from app.models.enums import CollectionType
from app.models.job_collection import JobCollection
from app.services import collection_dimensions, job_collections, standard_titles
from app.services.collection_dimensions import REMOTE_SLUG, matches_all
from app.services.collection_stats import CollectionStats, calculate_collection_stats
from app.services.static_hub_pages import HUB_ROOT, _board, _newest_first, _trail, _url, render_hub_gone
from app.services.static_job_pages import (
    CACHE_CONTROL, COUNTRY_SLUG, LinkJob, breadcrumb_ld, eastern, ld_json, slugify,
)
from app.services.static_pages import _TEMPLATE_ENV, read_json, upload_html, write_json

logger = logging.getLogger("app.static_collection_pages")

COLLECTION_MANIFEST_KEY = "_meta/collection-pages.json"
PER_PAGE = 50
MAX_PAGES = 10  # past this, the board has the rest
RENDERED_TYPES = frozenset({CollectionType.ROLE, CollectionType.ROLE_REMOTE, CollectionType.ROLE_LOCATION})
LIVE_STATUSES = frozenset({S.PUBLISHED, S.INDEXABLE, S.PERFORMING, S.DECLINING, S.NOINDEX})
# First segments under /jobs/us/ that other pages own (sector slugs aside).
RESERVED_SEGMENTS = frozenset({"companies", "locations", "other", "_assets", "_meta", "_share"})
PAGE_SEGMENT = "page"
RELATED_MAX = 12
JOB_LINKS_MAX = 2  # a job page's links to its collections: the role, and its remote page
COUNTRY_ROLES_MAX = 12  # role pages listed on /jobs/us


def collection_path(collection: JobCollection) -> str:
    path = f"{HUB_ROOT}/{collection.role}"
    return f"{path}/{collection.location}" if collection.location else path


def page_path(base: str, number: int) -> str:
    return base if number == 1 else f"{base}/{PAGE_SEGMENT}/{number}"


@dataclass
class CollectionPage:
    path: str  # page 1's
    collection: JobCollection
    title: str  # <title>, without the brand
    h1: str
    description: str
    stats: CollectionStats
    jobs: list[LinkJob]  # newest first, every page's
    indexable: bool
    trail: list[tuple[str, str | None]]
    board: tuple[str, str]
    related: list[tuple[str, str]] = field(default_factory=list)  # (label, absolute url)

    @property
    def page_count(self) -> int:
        return max(1, min(MAX_PAGES, math.ceil(len(self.jobs) / PER_PAGE)))

    def paths(self) -> list[str]:
        return [page_path(self.path, n) for n in range(1, self.page_count + 1)]


def plan_collection_pages(db: Session, live: list[LinkJob], now: datetime) -> list[CollectionPage]:
    """A page for every live, renderable collection, from the open jobs in
    `live` (the generator's own list, so every job linked has a page)."""
    if not settings.job_collections_enabled:
        return []
    collections = db.scalars(
        select(JobCollection)
        .where(JobCollection.status.in_(LIVE_STATUSES), JobCollection.collection_type.in_(RENDERED_TYPES))
        .order_by(JobCollection.key)
    ).all()
    by_title: dict[str, list[LinkJob]] = defaultdict(list)
    for job in live:
        if not job.closed:
            for title in job.standard_titles:
                by_title[title].append(job)

    pages: list[CollectionPage] = []
    for collection in collections:
        dims = job_collections.dimensions(collection)
        role = collection_dimensions.get("role", collection.role)
        members = [job for job in by_title.get(role.name, ()) if matches_all(job, dims, now)]
        if members:  # none open: its page is retired like a removed one
            pages.append(_page(collection, role.name, members, now))

    # Related: a role page's remote page first, then the role's related
    # standard titles that have a live page of the same kind (same
    # location, or none).
    by_key = {(p.collection.role, p.collection.location): p for p in pages}
    for page in pages:
        remote = by_key.get((page.collection.role, REMOTE_SLUG)) if page.collection.location is None else None
        if remote is not None:
            page.related.append((remote.h1, _url(remote.path)))
        related_titles = standard_titles.related([collection_dimensions.get("role", page.collection.role).name])
        for title in related_titles:
            other = by_key.get((slugify(title), page.collection.location))
            if other is not None and other is not page and len(page.related) < RELATED_MAX:
                page.related.append((other.h1, _url(other.path)))
    return pages


def _page(collection: JobCollection, role: str, members: list[LinkJob], now: datetime) -> CollectionPage:
    stats = calculate_collection_stats(members, [], now)
    base = collection_path(collection)
    updated = eastern(now).strftime("%B %-d, %Y")
    board = {"q": role, "sector": "all"}
    if collection.location == REMOTE_SLUG:
        h1, place = f"Remote {role} jobs", "remote"
        board["workplace"] = "remote"
    elif collection.location:
        area = collection_dimensions.get("location", collection.location)
        h1, place = f"{role} jobs in {area.name}", f"in {area.name}"
        board["metro"] = collection.location
    else:
        h1, place = f"{role} jobs", "in the US"
    title = collection.title or f"{h1[:1].upper()}{h1[1:]} ({stats.open_jobs:,} open)"
    description = collection.meta_description or (
        f"{stats.open_jobs:,} open {role} jobs {place} from {stats.companies_hiring:,} companies, "
        f"updated {updated}. Free to search and apply on Yabot Jobs."
    )
    crumbs = [(f"{role} jobs", _url(f"{HUB_ROOT}/{collection.role}"))] if collection.location else []
    return CollectionPage(
        path=base,
        collection=collection,
        title=title,
        h1=collection.h1 or h1,
        description=description,
        stats=stats,
        jobs=_newest_first(members),
        indexable=collection.is_indexable,
        trail=_trail(*crumbs, (collection.h1 or h1, None)),
        board=(f"Search all {role} jobs on Yabot", _board(**board)),
    )


class CollectionLinks:
    """Links to the indexed collection pages from the generator's other
    pages: job pages and the /jobs/us page. A NOINDEX collection keeps its
    page but isn't sent anyone's way."""

    def __init__(self, pages: list[CollectionPage]) -> None:
        self._pages = {(p.collection.role, p.collection.location): p for p in pages if p.indexable}

    def for_job(self, job: LinkJob) -> list[tuple[str, str]]:
        """(label, path): the page of the job's first standard title that
        has one, and that title's remote page when the job is remote."""
        for title in job.standard_titles:
            role = slugify(title)
            pages = [self._pages.get((role, None))]
            if job.workplace_type == "remote":
                pages.append(self._pages.get((role, REMOTE_SLUG)))
            links = [(p.h1, p.path) for p in pages if p is not None]
            if links:
                return links[:JOB_LINKS_MAX]
        return []

    def top_roles(self) -> list[tuple[str, str, int]]:
        """(name, path, open jobs) of the biggest role pages, for /jobs/us."""
        roles = [p for (_, location), p in self._pages.items() if location is None]
        roles.sort(key=lambda p: (-p.stats.open_jobs, p.h1))
        return [(p.h1.removesuffix(" jobs"), p.path, p.stats.open_jobs) for p in roles[:COUNTRY_ROLES_MAX]]


def render_collection_page(page: CollectionPage, number: int) -> str:
    url = _url(page_path(page.path, number))
    start = (number - 1) * PER_PAGE
    return _TEMPLATE_ENV.get_template("collection.html.jinja").render(
        page=page,
        number=number,
        jobs=page.jobs[start : start + PER_PAGE],
        first=start + 1,
        page_urls=[_url(p) for p in page.paths()],
        page_url=url,
        robots="index, follow" if page.indexable else "noindex, follow",
        ld_json=ld_json(breadcrumb_ld(page.trail, url)),
        base_url=settings.seo_pages_base_url,
        country_slug=COUNTRY_SLUG,
    )


def publish_collection_pages(pages: list[CollectionPage], pool) -> list[str]:
    """Upload every page of every collection, a noindex page for any path
    published before that's gone, and record what's live. Returns the live
    paths, for invalidation."""
    previous = set((read_json(COLLECTION_MANIFEST_KEY) or {}).get("paths", []))
    uploads = [(path, page, n) for page in pages for n, path in enumerate(page.paths(), start=1)]
    live = sorted(path for path, _, _ in uploads)
    retired = sorted(previous - set(live))

    def publish(item) -> None:
        path, page, n = item
        upload_html(path, render_collection_page(page, n) if page else render_hub_gone(path), CACHE_CONTROL)

    list(pool.map(publish, uploads + [(path, None, 0) for path in retired]))
    if pages or previous:
        write_json(COLLECTION_MANIFEST_KEY, {"paths": live})
    logger.info("Published %d collection page(s) for %d collection(s); %d retired.", len(live), len(pages), len(retired))
    return live + retired
