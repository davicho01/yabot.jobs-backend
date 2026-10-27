"""Whether a job collection is worth a page (spec §13, §14): a weighted
score from what its jobs show, gates the score can't buy past, and a
recommendation — INDEX, REVIEW or NOINDEX. Deterministic and computed, like
the stats it reads (collection_stats); no model is asked.

  inventory           25  open jobs, full marks at THRESHOLDS.inventory_full
  employer diversity  15  companies hiring, docked when one company dominates
  freshness           20  share of jobs found, posted or verified recently
  data quality        10  share with a salary, a workplace type, an
                          employer's date and a location
  demand              15  search demand — no data until Search Console
                          ingestion (PR-16), so None for now
  historical stability 10 weeks out of the last four that brought new jobs
  uniqueness           5  how different it is from the collection it
                          narrows (role + remote vs role): a near-copy
                          of its parent adds nothing

A component without data (demand, for now) or that doesn't apply (employer
diversity for a single company's page) is None and left out: the total is
the points scored out of the weights that count, scaled to 100, so a
missing signal neither sinks nor inflates a collection.

Policy (spec §14), thresholds in Thresholds: INDEX at a total of
index_score or more with no issue (too few jobs or employers, one employer
dominating, a near-copy of its parent, stale); REVIEW from review_score;
NOINDEX below. Report-only for now: nothing publishes on a recommendation,
and in Phase 1 only a person ever does (job_collections.MANUAL_ONLY).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Iterable, Sequence

from app.models.enums import WorkplaceType
from app.models.job_collection import JobCollection
from app.services import job_collections
from app.services.collection_dimensions import matches_all
from app.services.collection_stats import CollectionStats, StatsJob, calculate_collection_stats
from app.services.static_job_pages import eastern

WEIGHTS = {
    "inventory": 25,
    "employer_diversity": 15,
    "freshness": 20,
    "data_quality": 10,
    "demand": 15,
    "stability": 10,
    "uniqueness": 5,
}


@dataclass(frozen=True)
class Thresholds:
    index_score: int = 80
    review_score: int = 60
    min_jobs: int = 20  # fewer is too_few_jobs
    inventory_full: int = 100
    min_companies: int = 5  # fewer is too_few_employers
    companies_full: int = 20
    max_top_company_share: float = 0.5  # more is one_employer_dominates
    fresh_days: int = 7  # found or posted within this many Eastern days ...
    verified_days: int = 2  # ... or still listed by a crawl this recently
    fresh_full_share: float = 0.5
    stability_weeks: int = 4
    unique_full_share: float = 0.5  # at most this share of its parent: full marks
    near_duplicate_share: float = 0.9  # at least this share: near_duplicate
    stale_days: int = 14  # nothing found or verified for this long: stale


THRESHOLDS = Thresholds()


class Recommendation(StrEnum):
    INDEX = "index"
    REVIEW = "review"
    NOINDEX = "noindex"


@dataclass(frozen=True)
class Evaluation:
    inventory_score: float
    employer_diversity_score: float | None
    freshness_score: float
    data_quality_score: float
    demand_score: float | None
    stability_score: float
    uniqueness_score: float
    total: int  # out of 100, over the components that count
    recommendation: Recommendation
    issues: tuple[str, ...]
    stats: CollectionStats = field(repr=False)

    def as_dict(self) -> dict:
        """JSON-ready, for job_collections.qualification and reports."""
        out = asdict(self)
        out["recommendation"] = self.recommendation.value
        out["issues"] = list(self.issues)
        return out


def evaluate_jobs(
    jobs: Iterable[StatsJob],
    now: datetime,
    *,
    parent_size: int | None = None,
    single_employer: bool = False,
    thresholds: Thresholds = THRESHOLDS,
) -> Evaluation:
    """Score the open jobs among `jobs` as one collection. `parent_size`:
    open jobs in the collection this one narrows (None: it narrows nothing).
    `single_employer`: one company's page, where diversity doesn't apply."""
    t = thresholds
    members = [job for job in jobs if not job.closed]
    stats = calculate_collection_stats(members, [], now)
    n = stats.open_jobs
    issues: list[str] = []

    inventory = _share(n, t.inventory_full)
    if n < t.min_jobs:
        issues.append("too_few_jobs")

    diversity = None
    if not single_employer:
        top = Counter(job.company_key or job.domain for job in members).most_common(1)
        top_share = top[0][1] / n if n else 0.0
        concentration = 1.0 if top_share <= t.max_top_company_share else (1 - top_share) / (1 - t.max_top_company_share)
        diversity = _share(stats.companies_hiring, t.companies_full) * concentration
        if stats.companies_hiring < t.min_companies:
            issues.append("too_few_employers")
        if top_share > t.max_top_company_share:
            issues.append("one_employer_dominates")

    today = eastern(now).date()
    fresh_from = today - timedelta(days=t.fresh_days - 1)

    def fresh(job: StatsJob) -> bool:
        return (
            eastern(job.found_at).date() >= fresh_from
            or (job.posted_at is not None and fresh_from <= job.posted_at <= today)
            or (job.last_seen_at is not None and _since(now, job.last_seen_at) <= timedelta(days=t.verified_days))
        )

    freshness = _share(sum(map(fresh, members)), n * t.fresh_full_share) if n else 0.0

    complete = (
        stats.jobs_with_salary
        + sum(1 for job in members if job.workplace_type != WorkplaceType.UNKNOWN)
        + sum(1 for job in members if job.posted_at is not None)
        + sum(1 for job in members if job.metros or job.workplace_type == WorkplaceType.REMOTE)
    )
    quality = complete / (4 * n) if n else 0.0

    ages = [_since(now, job.found_at) for job in members]
    weeks_with_new = {age // timedelta(days=7) for age in ages if age >= timedelta(0)}
    stability = len(weeks_with_new & set(range(t.stability_weeks))) / t.stability_weeks

    if parent_size:
        within = n / parent_size
        uniqueness = _share(1 - within, 1 - t.unique_full_share)
        if within >= t.near_duplicate_share:
            issues.append("near_duplicate")
    else:
        uniqueness = 1.0

    seen = ages + [_since(now, job.last_seen_at) for job in members if job.last_seen_at]
    if n and min(seen) > timedelta(days=t.stale_days):
        issues.append("stale")

    shares = {
        "inventory": inventory,
        "employer_diversity": diversity,
        "freshness": freshness,
        "data_quality": quality,
        "demand": None,  # PR-16
        "stability": stability,
        "uniqueness": uniqueness,
    }
    scores = {k: None if s is None else round(s * WEIGHTS[k], 1) for k, s in shares.items()}
    out_of = sum(WEIGHTS[k] for k, s in scores.items() if s is not None)
    total = round(100 * sum(s for s in scores.values() if s is not None) / out_of)
    if total >= t.index_score and not issues:
        recommendation = Recommendation.INDEX
    elif total >= t.review_score:
        recommendation = Recommendation.REVIEW
    else:
        recommendation = Recommendation.NOINDEX
    return Evaluation(**{f"{k}_score": s for k, s in scores.items()}, total=total,
                      recommendation=recommendation, issues=tuple(issues), stats=stats)


def evaluate_collection(
    jobs: Sequence[StatsJob], collection: JobCollection, now: datetime, thresholds: Thresholds = THRESHOLDS
) -> Evaluation:
    """Score a registered collection over `jobs` (the generator's live
    index). Its parent, for uniqueness, is the smallest collection left by
    dropping one of its dimensions."""
    dims = job_collections.dimensions(collection)
    open_jobs = [job for job in jobs if not job.closed]
    members = [job for job in open_jobs if matches_all(job, dims, now)]
    parents = [
        sum(1 for job in open_jobs if matches_all(job, [d for d in dims if d is not dropped], now))
        for dropped in dims
    ] if len(dims) > 1 else []
    return evaluate_jobs(members, now, parent_size=min(parents) if parents else None, thresholds=thresholds)


def _share(value: float, full: float) -> float:
    return min(1.0, value / full) if full > 0 else 1.0


def _since(now: datetime, moment: datetime) -> timedelta:
    return eastern(now) - eastern(moment)  # eastern() reads SQLite's naive UTC too
