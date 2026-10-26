"""The dimensions a job collection is built from, and which jobs belong to
each — the vocabulary the collection pages (still to come) are defined in,
taken entirely from fields the jobs already carry (ADR-005, ADR-010):

  category   the job's sector, by the static pages' own slugs ("healthcare")
  role       one of the 103 standard tech titles ("software-engineer") the
             sector service tags Tech postings with (app.services.standard_titles)
  location   a geo metro/micro area or state, by the board's own ?metro=
             slugs ("raleigh-nc", "north-carolina"), or "remote"
  salary     a pay floor ("100k-plus")
  freshness  posted, found or verified today / this week ("posted-today")

Each Dimension answers the same question twice, identically: matches() for a
job already in memory (the static generator's live index) and clause() as a
SQLAlchemy filter on JobPosting/JobPostingUrl (admin and API use). Both take
`now`, which only freshness reads.

Freshness keeps the three dates apart and never substitutes one for another:
posted is the employer's own date (posted_at — a job with none is never
"posted" anything), found is when we first saw the URL (job_posting_urls.
created_at), verified is when a crawl last listed it (last_seen_at — never
set for a user-submitted URL). Days are US Eastern, like every date on the
static pages. A job found today but posted a month ago is found today, not
posted today.

Salary has no pay period stored, so a floor counts only the low end of the
listed range (salary_min, else salary_max) and only in USD or no currency
(postings are overwhelmingly USD). Every floor is far above any hourly,
weekly or monthly figure, so what passes is an annual salary; anything above
SALARY_PLAUSIBLE_MAX is a parsing error, not a salary.

US-only, like the static pages (Q-014): callers combine these with the live
job filter (static_job_pages._eligible_filter), which already is.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Protocol

from sqlalchemy import ColumnElement, and_, func, or_

from app.models.enums import WorkplaceType
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services import geo, standard_titles
from app.services.static_job_pages import eastern, slugify
from app.services.static_pages import SECTOR_DISPLAY_NAMES, SECTORS_BY_SLUG, day_bounds_utc

REMOTE_SLUG = "remote"
SALARY_FLOORS = (50_000, 75_000, 100_000, 150_000, 200_000)
SALARY_PLAUSIBLE_MAX = 2_000_000
_SALARY_CURRENCIES = (None, "USD")


class DimensionJob(Protocol):
    """What membership reads from a job — LinkJob's names, so the generator's
    live index can be checked directly once it carries these fields."""

    sector: str
    standard_titles: list[str] | tuple[str, ...] | None
    metros: list[str] | tuple[str, ...]
    workplace_type: str
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    posted_at: date | None
    found_at: datetime
    last_seen_at: datetime | None


@dataclass(frozen=True)
class Dimension:
    kind: str  # "category" | "role" | "location" | "salary" | "freshness"
    slug: str
    name: str  # display: "Healthcare", "Software Engineer", "Raleigh, NC", "$100k+", "Posted today"
    _matches: Callable[[DimensionJob, datetime], bool]
    _clause: Callable[[datetime], ColumnElement[bool]]

    def matches(self, job: DimensionJob, now: datetime) -> bool:
        return self._matches(job, now)

    def clause(self, now: datetime) -> ColumnElement[bool]:
        return self._clause(now)


def matches_all(job: DimensionJob, dimensions: list[Dimension], now: datetime) -> bool:
    return all(d.matches(job, now) for d in dimensions)


def clause_all(dimensions: list[Dimension], now: datetime) -> ColumnElement[bool]:
    return and_(*(d.clause(now) for d in dimensions))


# --------------------------------------------------------------- category


def category(slug: str) -> Dimension | None:
    sector = SECTORS_BY_SLUG.get(slug)  # no UNKNOWN: it isn't a real category
    if sector is None:
        return None
    return Dimension(
        "category", slug, SECTOR_DISPLAY_NAMES[sector],
        lambda job, now: job.sector == sector,
        lambda now: JobPosting.sector == sector.value,
    )


# ------------------------------------------------------------------- role


@functools.cache
def _titles_by_slug() -> dict[str, str]:
    return {slugify(title): title for title in standard_titles.list_titles()}


def role_slugs() -> list[str]:
    return list(_titles_by_slug())


def role(slug: str) -> Dimension | None:
    """Jobs the model tagged with this exact title. Not its related titles,
    as title search adds: a "Software Engineer" page lists software engineers."""
    title = _titles_by_slug().get(slug)
    if title is None:
        return None
    return Dimension(
        "role", slug, title,
        lambda job, now: title in (job.standard_titles or ()),
        # @> — what ix_job_postings_standard_titles (GIN, jsonb_path_ops) serves
        lambda now: JobPosting.standard_titles.contains([title]),
    )


# --------------------------------------------------------------- location


def location(slug: str) -> Dimension | None:
    if slug == REMOTE_SLUG:
        return Dimension(
            "location", slug, "Remote",
            lambda job, now: job.workplace_type == WorkplaceType.REMOTE,
            lambda now: JobPosting.workplace_type == WorkplaceType.REMOTE.value,
        )
    area = geo.metro_by_slug(slug)
    if area is None:
        return None
    return Dimension(
        "location", slug, area.name,
        lambda job, now: area.code in job.metros,
        # @> — what ix_job_postings_metros (GIN, jsonb_path_ops) serves
        lambda now: JobPosting.metros.contains([area.code]),
    )


# ----------------------------------------------------------------- salary


def _salary_slug(floor: int) -> str:
    return f"{floor // 1000}k-plus"


def salary_slugs() -> list[str]:
    return [_salary_slug(floor) for floor in SALARY_FLOORS]


def salary(slug: str) -> Dimension | None:
    floor = next((f for f in SALARY_FLOORS if _salary_slug(f) == slug), None)
    if floor is None:
        return None

    def matches(job: DimensionJob, now: datetime) -> bool:
        low = job.salary_min if job.salary_min is not None else job.salary_max
        return low is not None and floor <= low <= SALARY_PLAUSIBLE_MAX and job.salary_currency in _SALARY_CURRENCIES

    def clause(now: datetime) -> ColumnElement[bool]:
        low = func.coalesce(JobPosting.salary_min, JobPosting.salary_max)
        return and_(
            low >= floor,
            low <= SALARY_PLAUSIBLE_MAX,
            or_(JobPosting.salary_currency.is_(None), JobPosting.salary_currency == "USD"),
        )

    return Dimension("salary", slug, f"${floor // 1000}k+", matches, clause)


# -------------------------------------------------------------- freshness

# slug: (basis, Eastern days counted back including today, display name)
_FRESHNESS: dict[str, tuple[str, int, str]] = {
    "posted-today": ("posted", 1, "Posted today"),
    "posted-this-week": ("posted", 7, "Posted this week"),
    "found-today": ("found", 1, "Found today"),
    "found-this-week": ("found", 7, "Found this week"),
    "verified-today": ("verified", 1, "Verified today"),
}


def freshness_slugs() -> list[str]:
    return list(_FRESHNESS)


def _first_day(now: datetime, days: int) -> date:
    return eastern(now).date() - timedelta(days=days - 1)


def freshness(slug: str) -> Dimension | None:
    if slug not in _FRESHNESS:
        return None
    basis, days, name = _FRESHNESS[slug]

    if basis == "posted":
        # A date with no time: between the first day and today (a future
        # posted_at is bad data, not a fresh job).
        def matches(job: DimensionJob, now: datetime) -> bool:
            return job.posted_at is not None and _first_day(now, days) <= job.posted_at <= eastern(now).date()

        def clause(now: datetime) -> ColumnElement[bool]:
            return JobPosting.posted_at.between(_first_day(now, days), eastern(now).date())

    else:
        column = JobPostingUrl.created_at if basis == "found" else JobPostingUrl.last_seen_at

        def moment(job: DimensionJob) -> datetime | None:
            return job.found_at if basis == "found" else job.last_seen_at

        def matches(job: DimensionJob, now: datetime) -> bool:
            at = moment(job)
            return at is not None and _first_day(now, days) <= eastern(at).date() <= eastern(now).date()

        def clause(now: datetime) -> ColumnElement[bool]:
            start, _ = day_bounds_utc(_first_day(now, days))
            _, end = day_bounds_utc(eastern(now).date())
            return and_(column >= start, column < end)

    return Dimension("freshness", slug, name, matches, clause)


# ------------------------------------------------------------------ lookup

_BY_KIND: dict[str, Callable[[str], Dimension | None]] = {
    "category": category,
    "role": role,
    "location": location,
    "salary": salary,
    "freshness": freshness,
}


def get(kind: str, slug: str) -> Dimension | None:
    """The dimension `slug` names, or None for an unknown kind or slug."""
    build = _BY_KIND.get(kind)
    return build(slug) if build else None
