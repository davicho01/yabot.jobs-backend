"""Static hub pages that group the live job pages (app.services.static_job_pages)
by company and by place — what "<sector> jobs in <city>" and "jobs at
<company>" searches land on, and the internal links that let crawlers reach
every job page from somewhere other than the sitemap:

  /jobs/us/companies/<company>          every live job at a company
  /jobs/us/locations/<metro>            jobs in a metro/micro area ("raleigh-nc")
  /jobs/us/locations/<metro>/<sector>   e.g. engineering jobs in Raleigh
  /jobs/us/locations/<state>            a state's metros and jobs ("north-carolina")
  /jobs/us/locations/remote[/<sector>]  US-remote jobs

Metro and state slugs are app.services.geo's own (the same ones the board's
?metro= filter takes), and the two can't collide: a metro slug ends in its
state's abbreviation. A hub only exists above a minimum number of live jobs,
so there are no near-empty pages; one that drops below it gets a noindex
page (tracked in _meta/hub-pages.json). Everything is rebuilt from the live
jobs on every run — a few thousand small pages.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlencode

from app.core.config import settings
from app.services import geo
from app.services.static_job_pages import (
    CACHE_CONTROL,
    COUNTRY_SLUG,
    OTHER_SECTOR_SLUG,
    LinkJob,
    _sector_link,
    breadcrumb_ld,
    ld_json,
    sector_slug,
    slugify,
)
from app.services.static_pages import _TEMPLATE_ENV, COUNTRY_NAMES, DAY_BOUNDARY_TZ, read_json, upload_html, write_json

logger = logging.getLogger("app.static_hub_pages")

HUB_ROOT = f"jobs/{COUNTRY_SLUG}"
HUB_MANIFEST_KEY = "_meta/hub-pages.json"
COMPANY_MIN_JOBS = 2
LOCATION_MIN_JOBS = 5
PER_SECTION = 25  # jobs listed per sector on a company/metro/state/remote hub
COMPANY_MAX_JOBS = 300
METRO_SECTOR_MAX_JOBS = 200
COUNTRY_TOP = 30  # companies/locations listed on the country page


def company_hub_path(slug: str) -> str:
    return f"{HUB_ROOT}/companies/{slug}"


def location_hub_path(slug: str, sector: str | None = None) -> str:
    return f"{HUB_ROOT}/locations/{slug}" + (f"/{sector}" if sector else "")


def _newest_first(jobs: Iterable[LinkJob]) -> list[LinkJob]:
    return sorted(jobs, key=lambda j: (j.day, j.found_at), reverse=True)


@dataclass
class Section:
    heading: str
    jobs: list[LinkJob]
    total: int
    more: tuple[str, str] | None = None  # (label, absolute url)


@dataclass
class Hub:
    path: str
    title: str  # <title>, without the brand
    h1: str
    description: str
    trail: list[tuple[str, str | None]]
    count: int
    sections: list[Section] = field(default_factory=list)
    link_lists: list[tuple[str, list[tuple[str, str]]]] = field(default_factory=list)  # (heading, [(label, url)])
    board: tuple[str, str] | None = None  # (label, absolute url)


class HubPlan:
    """Which hubs exist this run, and the jobs on each. Built from the live
    jobs only, so a hub never links to a job whose page isn't up."""

    def __init__(self) -> None:
        self.pages: dict[str, Hub] = {}
        self._company_paths: dict[str, str] = {}  # company_key -> path
        self._metro_paths: dict[str, str] = {}  # metro/state code -> path
        self._metro_sector_paths: dict[tuple[str, str], str] = {}
        self._remote_paths: dict[str | None, str] = {}  # sector slug (None = all) -> path
        self._areas: dict[str, geo.Metro] = {}
        self._counts: dict[str, int] = {}

    # --- links from job pages ---------------------------------------------

    def company_path(self, job: LinkJob) -> str | None:
        return self._company_paths.get(job.company_key or "")

    def location_links(self, job: LinkJob) -> list[tuple[str, str]]:
        """(label, path) of the location hubs a job page links to: its first
        metro (and that metro × its sector), else its state; and remote."""
        links: list[tuple[str, str]] = []
        sector = sector_slug(job.sector)
        sector_name = (_sector_link(job.sector) or (None, None))[1]
        for code in job.metros:
            area = self._areas.get(code)
            if area is None or area.kind == "state" or code not in self._metro_paths:
                continue
            if sector_name and (code, sector) in self._metro_sector_paths:
                links.append((f"{sector_name} jobs in {area.name}", self._metro_sector_paths[(code, sector)]))
            links.append((f"All jobs in {area.name}", self._metro_paths[code]))
            break
        else:
            for code in job.metros:
                area = self._areas.get(code)
                if area is not None and area.kind == "state" and code in self._metro_paths:
                    links.append((f"Jobs in {area.name}", self._metro_paths[code]))
                    break
        if job.workplace_type == "remote":
            if sector_name and sector in self._remote_paths:
                links.append((f"Remote {sector_name} jobs", self._remote_paths[sector]))
            if None in self._remote_paths:
                links.append(("All remote jobs", self._remote_paths[None]))
        return links

    def country_links(self) -> dict:
        """Top company and location hubs for the /jobs/us page."""
        def top(kind: str) -> list[tuple[str, str, int]]:
            hubs = [h for h in self.pages.values() if h.path.startswith(f"{HUB_ROOT}/{kind}/") and h.path.count("/") == 3]
            hubs.sort(key=lambda h: (-h.count, h.h1))
            return [(h.h1.removeprefix("Jobs at ").removeprefix("Jobs in "), h.path, h.count) for h in hubs[:COUNTRY_TOP]]

        return {"companies": top("companies"), "locations": top("locations")}


def _url(path: str) -> str:
    return f"{settings.seo_pages_base_url}/{path}"


def _board(**params: str) -> str:
    return f"{settings.seo_pages_base_url}/jobs?{urlencode(params)}"


def _trail(*crumbs: tuple[str, str | None]) -> list[tuple[str, str | None]]:
    base = settings.seo_pages_base_url
    return [("Home", f"{base}/"), ("Jobs", f"{base}/jobs"), (COUNTRY_NAMES[COUNTRY_SLUG], f"{base}/{HUB_ROOT}"), *crumbs]


def _by_sector(jobs: list[LinkJob], more_link=None) -> list[Section]:
    """Sections per sector (largest first), PER_SECTION newest jobs each.
    `more_link(sector_slug, sector_name, count)` -> (label, url) or None."""
    groups: dict[str, list[LinkJob]] = defaultdict(list)
    for job in jobs:
        groups[sector_slug(job.sector)].append(job)
    sections = []
    for slug, group in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        name = "Other" if slug == OTHER_SECTOR_SLUG else (_sector_link(group[0].sector) or (None, slug))[1]
        sections.append(
            Section(
                heading=name,
                jobs=_newest_first(group)[:PER_SECTION],
                total=len(group),
                more=more_link(slug, name, len(group)) if more_link and len(group) > PER_SECTION else None,
            )
        )
    return sections


def _top_cities(jobs: list[LinkJob], limit: int = 15) -> list[str]:
    counts = Counter(city for city in (job.city for job in jobs) if city)
    return [city for city, _ in counts.most_common(limit)]


def plan_hubs(live: Iterable[LinkJob]) -> HubPlan:
    live = list(live)
    plan = HubPlan()
    updated = datetime.now(timezone.utc).astimezone(DAY_BOUNDARY_TZ).strftime("%B %-d, %Y")

    # --- companies ----------------------------------------------------------
    by_company: dict[str, list[LinkJob]] = defaultdict(list)
    for job in live:
        if job.company_key and slugify(job.company_key):
            by_company[job.company_key].append(job)
    company_slugs: dict[str, str] = {}
    for key, jobs in by_company.items():
        if len(jobs) < COMPANY_MIN_JOBS:
            continue
        slug = slugify(key, 80)
        if slug in company_slugs.values():  # two keys slugging alike: first one wins
            continue
        company_slugs[key] = slug
        name = Counter(j.company_display for j in jobs).most_common(1)[0][0]
        path = company_hub_path(slug)
        cities = _top_cities(jobs)
        plan.pages[path] = Hub(
            path=path,
            title=f"Jobs at {name} ({len(jobs):,} open)",
            h1=f"Jobs at {name}",
            description=f"{len(jobs):,} open jobs at {name}"
            + (f" in {', '.join(cities[:3])}" if cities else "")
            + f", updated {updated}. See every role and apply on Yabot Jobs.",
            trail=_trail((name, None)),
            count=len(jobs),
            sections=_by_sector(_newest_first(jobs)[:COMPANY_MAX_JOBS]),
            link_lists=[("Locations", [(city, "") for city in cities])] if cities else [],
            board=(f"Search all {name} jobs on Yabot", _board(company=name)),
        )
        plan._company_paths[key] = path

    # --- metros, states -------------------------------------------------------
    by_area: dict[str, list[LinkJob]] = defaultdict(list)
    for job in live:
        for code in job.metros:
            by_area[code].append(job)
    for code, jobs in by_area.items():
        area = geo.metro_by_code(code)
        if area is None or len(jobs) < LOCATION_MIN_JOBS:
            continue
        plan._areas[code] = area
        plan._metro_paths[code] = location_hub_path(area.slug)

    for code, path in plan._metro_paths.items():
        area, jobs = plan._areas[code], by_area[code]
        if area.kind == "state":
            continue
        sector_groups: dict[str, list[LinkJob]] = defaultdict(list)
        for job in jobs:
            slug = sector_slug(job.sector)
            if slug != OTHER_SECTOR_SLUG:
                sector_groups[slug].append(job)
        for slug, group in sector_groups.items():
            if len(group) < LOCATION_MIN_JOBS:
                continue
            sector_name = _sector_link(group[0].sector)[1]
            sub = location_hub_path(area.slug, slug)
            plan._metro_sector_paths[(code, slug)] = sub
            plan.pages[sub] = Hub(
                path=sub,
                title=f"{sector_name} Jobs in {area.name}",
                h1=f"{sector_name} jobs in {area.name}",
                description=f"{len(group):,} open {sector_name} jobs in the {area.title} area, updated {updated}. "
                "Free to search and apply on Yabot Jobs.",
                trail=_trail((f"Jobs in {area.name}", _url(path)), (sector_name, None)),
                count=len(group),
                sections=[Section(heading=f"{sector_name} jobs", jobs=_newest_first(group)[:METRO_SECTOR_MAX_JOBS], total=len(group))],
                link_lists=[("Nearby", [(f"All jobs in {area.name}", _url(path)), (f"{sector_name} jobs across the US", _url(f"{HUB_ROOT}/{slug}"))])],
                board=(f"Search {sector_name} jobs in {area.name}", _board(metro=area.slug)),
            )

        def more_link(slug, name, count, code=code, area=area):
            sub = plan._metro_sector_paths.get((code, slug))
            return (f"All {count:,} {name} jobs in {area.name}", _url(sub)) if sub else None

        cities = _top_cities(jobs)
        plan.pages[path] = Hub(
            path=path,
            title=f"Jobs in {area.name} ({len(jobs):,} open)",
            h1=f"Jobs in {area.name}",
            description=f"{len(jobs):,} open jobs in the {area.title} area"
            + (f", including {', '.join(cities[:3])}" if cities else "")
            + f". Updated {updated}; free to search and apply on Yabot Jobs.",
            trail=_trail((f"Jobs in {area.name}", None)),
            count=len(jobs),
            sections=_by_sector(jobs, more_link),
            link_lists=[("Cities in this area", [(city, "") for city in cities])] if cities else [],
            board=(f"Search all jobs in {area.name}", _board(metro=area.slug)),
        )

    for code, path in plan._metro_paths.items():
        area, jobs = plan._areas[code], by_area[code]
        if area.kind != "state":
            continue
        metros_here = sorted(
            (
                (plan._areas[c].name, _url(p), len(by_area[c]))
                for c, p in plan._metro_paths.items()
                if plan._areas[c].kind != "state" and plan._areas[c].name.endswith(f", {code}")
            ),
            key=lambda m: -m[2],
        )
        plan.pages[path] = Hub(
            path=path,
            title=f"Jobs in {area.name} ({len(jobs):,} open)",
            h1=f"Jobs in {area.name}",
            description=f"{len(jobs):,} open jobs across {area.name}"
            + (f", in {', '.join(m[0] for m in metros_here[:3])} and more" if metros_here else "")
            + f". Updated {updated}; free to search and apply on Yabot Jobs.",
            trail=_trail((f"Jobs in {area.name}", None)),
            count=len(jobs),
            sections=_by_sector(jobs),
            link_lists=[("Metro areas", [(f"{name} ({count:,})", url) for name, url, count in metros_here])] if metros_here else [],
            board=(f"Search all jobs in {area.name}", _board(metro=area.slug)),
        )

    # --- remote -------------------------------------------------------------
    remote = [job for job in live if job.workplace_type == "remote"]
    if len(remote) >= LOCATION_MIN_JOBS:
        remote_path = location_hub_path("remote")
        sector_groups: dict[str, list[LinkJob]] = defaultdict(list)
        for job in remote:
            slug = sector_slug(job.sector)
            if slug != OTHER_SECTOR_SLUG:
                sector_groups[slug].append(job)
        for slug, group in sector_groups.items():
            if len(group) < LOCATION_MIN_JOBS:
                continue
            sector_name = _sector_link(group[0].sector)[1]
            sub = location_hub_path("remote", slug)
            plan._remote_paths[slug] = sub
            plan.pages[sub] = Hub(
                path=sub,
                title=f"Remote {sector_name} Jobs ({len(group):,} open)",
                h1=f"Remote {sector_name} jobs",
                description=f"{len(group):,} open remote {sector_name} jobs in the US, updated {updated}. "
                "Free to search and apply on Yabot Jobs.",
                trail=_trail(("Remote jobs", _url(remote_path)), (sector_name, None)),
                count=len(group),
                sections=[Section(heading=f"Remote {sector_name} jobs", jobs=_newest_first(group)[:METRO_SECTOR_MAX_JOBS], total=len(group))],
                board=(f"Search remote {sector_name} jobs", _board(workplace="remote")),
            )
        plan._remote_paths[None] = remote_path

        def remote_more(slug, name, count):
            sub = plan._remote_paths.get(slug)
            return (f"All {count:,} remote {name} jobs", _url(sub)) if sub else None

        plan.pages[remote_path] = Hub(
            path=remote_path,
            title=f"Remote Jobs in the US ({len(remote):,} open)",
            h1="Remote jobs",
            description=f"{len(remote):,} open remote jobs in the US, updated {updated}. Free to search and apply on Yabot Jobs.",
            trail=_trail(("Remote jobs", None)),
            count=len(remote),
            sections=_by_sector(remote, remote_more),
            board=("Search all remote jobs", _board(workplace="remote")),
        )

    return plan


def render_hub(hub: Hub) -> str:
    page_url = _url(hub.path)
    return _TEMPLATE_ENV.get_template("hub.html.jinja").render(
        hub=hub,
        page_url=page_url,
        ld_json=ld_json(breadcrumb_ld(hub.trail, page_url)),
        base_url=settings.seo_pages_base_url,
        country_slug=COUNTRY_SLUG,
    )


def render_hub_gone(path: str) -> str:
    return _TEMPLATE_ENV.get_template("hub_gone.html.jinja").render(
        base_url=settings.seo_pages_base_url, country_slug=COUNTRY_SLUG
    )


def publish_hubs(plan: HubPlan, pool) -> list[str]:
    """Upload every hub in the plan, a noindex page for any hub published
    before that's no longer in it, and record what's live. Returns the live
    hub paths (for the sitemap)."""
    previous = set((read_json(HUB_MANIFEST_KEY) or {}).get("paths", []))
    live = sorted(plan.pages)
    retired = sorted(previous - set(live))

    # Each hub is rendered inside its own upload task, so only the pages in
    # flight are ever held in memory. Rendering all ~8k first (some run to
    # hundreds of KB) is what ran the first full backfill out of memory.
    def publish(path: str) -> None:
        html = render_hub(plan.pages[path]) if path in plan.pages else render_hub_gone(path)
        upload_html(path, html, CACHE_CONTROL)

    list(pool.map(publish, live + retired))
    write_json(HUB_MANIFEST_KEY, {"paths": live})
    logger.info("Published %d hub page(s); %d retired.", len(live), len(retired))
    return live
