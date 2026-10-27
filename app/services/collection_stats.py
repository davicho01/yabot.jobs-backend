"""A job collection's numbers (spec §12): how many open jobs, from how many
companies, how many remote/hybrid/on-site, how many list a salary, how many
were posted, found and verified in the last 24 hours, and the median salary
range. Plain counting, never a model's estimate.

Two ways to the same answer:

  calculate_collection_stats   over jobs already in memory — the static
                               generator's live index (LinkJob), so a run
                               adds no queries
  collection_stats_from_db     straight from the database, for admin/API use

Both count open jobs only (a closed job keeps its page but isn't listed
anywhere), from the same live set the job pages are built from
(static_job_pages._eligible_filter), and every other number is a subset of
those open jobs.

The 24-hour windows keep the three dates apart, like the freshness
dimensions: found is when we first saw the URL, verified is when a crawl
last listed it — both timestamps, so exactly the last 24 hours — and posted
is the employer's own date, which has no time: a job counts when its date
falls on a US Eastern day the window touches (today, or yesterday until
the window has left it). A job found today but posted a month ago is found
in the last 24 hours, not posted in them.

Salary, like the salary dimension: USD or no currency, and below
SALARY_PLAUSIBLE_MAX (above it is a parsing error). jobs_with_salary counts
any listed figure; the medians use annual figures only — 1000 and up, the
same cut the job pages use to call a figure hourly — so hourly rates don't
drag the median of a mixed collection down to nothing.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Protocol

from sqlalchemy import case, func, or_, select, true
from sqlalchemy.orm import Session

from app.models.enums import WorkplaceType
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services.collection_dimensions import (
    SALARY_CURRENCIES,
    SALARY_PLAUSIBLE_MAX,
    Dimension,
    DimensionJob,
    clause_all,
    matches_all,
)
from app.services.static_job_pages import _eligible_filter, eastern

WINDOW = timedelta(hours=24)
ANNUAL_SALARY_MIN = 1000  # under this a figure is hourly (static_job_pages' unitText)


class StatsJob(DimensionJob, Protocol):
    company_key: str | None
    domain: str
    closed: str  # "" while open, as LinkJob has it


@dataclass(frozen=True)
class CollectionStats:
    open_jobs: int
    companies_hiring: int
    remote_jobs: int
    hybrid_jobs: int
    onsite_jobs: int
    jobs_with_salary: int
    jobs_posted_24h: int
    jobs_found_24h: int
    jobs_verified_24h: int
    median_salary_min: int | None
    median_salary_max: int | None

    def as_dict(self) -> dict[str, int | None]:
        return asdict(self)


def calculate_collection_stats(jobs: Iterable[StatsJob], dimensions: list[Dimension], now: datetime) -> CollectionStats:
    """The collection's numbers over `jobs` (the generator's live index)."""
    members = [job for job in jobs if not job.closed and matches_all(job, dimensions, now)]
    first_posted_day, since = _posted_from(now), now - WINDOW
    workplace = [job.workplace_type for job in members]
    salaried = [job for job in members if _salary_listed(job.salary_min, job.salary_max, job.salary_currency)]
    return CollectionStats(
        open_jobs=len(members),
        companies_hiring=len({job.company_key or job.domain for job in members}),
        remote_jobs=workplace.count(WorkplaceType.REMOTE),
        hybrid_jobs=workplace.count(WorkplaceType.HYBRID),
        onsite_jobs=workplace.count(WorkplaceType.ONSITE),
        jobs_with_salary=len(salaried),
        jobs_posted_24h=sum(1 for job in members if job.posted_at is not None and first_posted_day <= job.posted_at <= _today(now)),
        jobs_found_24h=sum(1 for job in members if since < _utc(job.found_at) <= now),
        jobs_verified_24h=sum(1 for job in members if job.last_seen_at is not None and since < _utc(job.last_seen_at) <= now),
        median_salary_min=_median(job.salary_min for job in salaried),
        median_salary_max=_median(job.salary_max for job in salaried),
    )


def collection_stats_from_db(db: Session, dimensions: list[Dimension], now: datetime) -> CollectionStats:
    """The same numbers as calculate_collection_stats, from the database:
    one aggregate query for the counts and one for the salary figures."""
    where = (
        _eligible_filter(now),
        JobPostingUrl.closed_at.is_(None),
        clause_all(dimensions, now) if dimensions else true(),
    )
    since = now - WINDOW

    def count_if(condition) -> object:
        return func.count(case((condition, 1)))

    salary_listed = _salary_listed_clause()
    counts = db.execute(
        select(
            func.count(),
            func.count(func.distinct(func.coalesce(JobPosting.company_key, JobPostingUrl.domain))),
            count_if(JobPosting.workplace_type == WorkplaceType.REMOTE.value),
            count_if(JobPosting.workplace_type == WorkplaceType.HYBRID.value),
            count_if(JobPosting.workplace_type == WorkplaceType.ONSITE.value),
            count_if(salary_listed),
            count_if(JobPosting.posted_at.between(_posted_from(now), _today(now))),
            count_if((JobPostingUrl.created_at > since) & (JobPostingUrl.created_at <= now)),
            count_if((JobPostingUrl.last_seen_at > since) & (JobPostingUrl.last_seen_at <= now)),
        )
        .select_from(JobPostingUrl)
        .join(JobPosting, JobPosting.url_id == JobPostingUrl.id)
        .where(*where)
    ).one()
    salaries = db.execute(
        select(JobPosting.salary_min, JobPosting.salary_max)
        .select_from(JobPostingUrl)
        .join(JobPosting, JobPosting.url_id == JobPostingUrl.id)
        .where(*where, salary_listed)
    ).all()
    return CollectionStats(
        *counts,
        median_salary_min=_median(low for low, _ in salaries),
        median_salary_max=_median(high for _, high in salaries),
    )


def _today(now: datetime) -> date:
    return eastern(now).date()


def _posted_from(now: datetime) -> date:
    """The first Eastern day the last 24 hours touch."""
    return eastern(now - WINDOW).date()


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)  # SQLite: naive UTC


def _plausible(value: int | None) -> bool:
    return value is not None and 0 < value <= SALARY_PLAUSIBLE_MAX


def _salary_listed(low: int | None, high: int | None, currency: str | None) -> bool:
    return currency in SALARY_CURRENCIES and (_plausible(low) or _plausible(high))


def _salary_listed_clause():
    def plausible(column):
        return (column > 0) & (column <= SALARY_PLAUSIBLE_MAX)

    return (
        or_(JobPosting.salary_currency.is_(None), JobPosting.salary_currency == "USD")
        & or_(plausible(JobPosting.salary_min), plausible(JobPosting.salary_max))
    )


def _median(values: Iterable[int | None]) -> int | None:
    annual = [v for v in values if _plausible(v) and v >= ANNUAL_SALARY_MIN]
    return round(statistics.median(annual)) if annual else None
