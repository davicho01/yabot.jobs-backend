"""Keeps the collection registry current (spec §11, PR-14): re-scores every
collection that isn't archived, records what the admin page shows about it,
and applies the automatic lifecycle rules. Run nightly by
collection_maintenance.py, before discovery; one collection can be
re-scored on demand from the admin page (reevaluate_one).

Recorded in its qualification, beside the score:
  below_bar_since  the Eastern date it first scored NOINDEX this time
  empty_since      the Eastern date it first had no open jobs this time
  duplicates       keys of other collections listing nearly the same jobs
                   (at least THRESHOLDS.near_duplicate_share of the longer
                   list), for the admin to pick one

Rules, each recorded as an event with its reason:
  indexed, scoring NOINDEX for GRACE_DAYS     -> NOINDEX   (lifecycle),
    unless a person approved it since it dropped below the bar
  any but archived, no open jobs for EMPTY_DAYS -> ARCHIVED (lifecycle)
  waiting: QUALIFIED no longer scoring INDEX  -> CANDIDATE (qualification)
           CANDIDATE now scoring INDEX        -> QUALIFIED (qualification)

Nothing here moves a collection into the index: that stays a person's call
(job_collections.MANUAL_ONLY), and a page taken out comes back by approving
it again. PERFORMING and DECLINING wait for search data (Phase 3).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.enums import CollectionStatus as S
from app.models.enums import CollectionTrigger as T
from app.models.job_collection import JobCollection
from app.services import collection_dimensions, job_collections
from app.services.collection_dimensions import clause_all, matches_all
from app.services.collection_qualification import THRESHOLDS, Evaluation, Recommendation, evaluate_jobs
from app.services.static_job_pages import LinkJob, eastern, load_link_index

GRACE_DAYS = 3
EMPTY_DAYS = 14
INDEXED = frozenset({S.PUBLISHED, S.INDEXABLE, S.PERFORMING, S.DECLINING})


@dataclass(frozen=True)
class Change:
    key: str
    previous: S
    new: S
    reason: str


def evaluate(collection: JobCollection, role_jobs: list[LinkJob], now: datetime) -> tuple[Evaluation, set[str]]:
    """Its score over the open jobs of its role (`role_jobs`), and the
    url_ids of its own jobs. A role + location is scored against its role."""
    dims = job_collections.dimensions(collection)
    members = [job for job in role_jobs if not job.closed and matches_all(job, dims, now)]
    parent = sum(1 for job in role_jobs if not job.closed) if collection.location else None
    return evaluate_jobs(members, now, parent_size=parent), {job.url_id for job in members}


def reevaluate_all(db: Session, live: list[LinkJob], now: datetime, *, apply: bool) -> list[Change]:
    """Re-score every collection that isn't archived over `live` (the
    generator's index) and apply the rules; the changes made, or that would
    be without `apply`. Commits when `apply`."""
    collections = db.scalars(
        select(JobCollection).where(JobCollection.status != S.ARCHIVED).order_by(JobCollection.key)
        .options(selectinload(JobCollection.events))
    ).all()
    by_title: dict[str, list[LinkJob]] = defaultdict(list)
    for job in live:
        for title in job.standard_titles:
            by_title[title].append(job)

    scored = {}
    for collection in collections:
        role = collection_dimensions.get("role", collection.role) if collection.role else None
        if role is None:  # only role collections have pages, and jobs to count, so far
            continue
        scored[collection.key] = (collection, *evaluate(collection, by_title.get(role.name, []), now))

    duplicates = _duplicates({key: ids for key, (_, _, ids) in scored.items()})
    changes = []
    for key, (collection, evaluation, _) in scored.items():
        changes += _record(db, collection, evaluation, duplicates.get(key, []), now, apply=apply)
    if apply:
        db.commit()
    return changes


def reevaluate_one(db: Session, collection: JobCollection, now: datetime) -> list[Change]:
    """Re-score one collection from the database (the admin's Re-evaluate)
    and apply the rules. Its duplicates are left as last found: finding
    them takes every collection's jobs. Flushes; the caller commits."""
    role = collection_dimensions.get("role", collection.role) if collection.role else None
    if role is None:
        raise job_collections.CollectionError(f"{collection.key}: only role collections can be scored so far")
    role_jobs = list(load_link_index(db, now, where=clause_all([role], now)).values())
    evaluation, _ = evaluate(collection, role_jobs, now)
    previous = (collection.qualification or {}).get("duplicates", [])
    changes = _record(db, collection, evaluation, previous, now, apply=True)
    db.flush()
    return changes


def _duplicates(jobs: dict[str, set[str]]) -> dict[str, list[str]]:
    """{key: other keys sharing at least near_duplicate_share of the longer
    job list}. Only pairs that share a job are compared."""
    by_job: dict[str, list[str]] = defaultdict(list)
    for key, ids in jobs.items():
        for url_id in ids:
            by_job[url_id].append(key)
    shared = Counter(pair for keys in by_job.values() for pair in combinations(sorted(keys), 2))
    out: dict[str, list[str]] = defaultdict(list)
    for (a, b), n in sorted(shared.items()):
        if n / max(len(jobs[a]), len(jobs[b])) >= THRESHOLDS.near_duplicate_share:
            out[a].append(b)
            out[b].append(a)
    return out


def _record(
    db: Session, collection: JobCollection, evaluation: Evaluation, duplicates: list[str], now: datetime, *, apply: bool
) -> list[Change]:
    today = eastern(now).date()
    previous = collection.qualification or {}
    below = evaluation.recommendation == Recommendation.NOINDEX
    empty = evaluation.stats.open_jobs == 0
    below_since = (previous.get("below_bar_since") or today.isoformat()) if below else None
    empty_since = (previous.get("empty_since") or today.isoformat()) if empty else None

    status = S(collection.status)
    change = None
    if empty_since and _days(today, empty_since) >= EMPTY_DAYS:
        change = (S.ARCHIVED, T.LIFECYCLE, f"no open jobs since {empty_since}")
    elif (
        status in INDEXED and below_since and _days(today, below_since) >= GRACE_DAYS
        and not _approved_since(collection, below_since)
    ):
        change = (S.NOINDEX, T.LIFECYCLE, f"scored {evaluation.total} (below the bar) since {below_since}")
    elif status == S.QUALIFIED and evaluation.recommendation != Recommendation.INDEX:
        change = (S.CANDIDATE, T.QUALIFICATION, f"scored {evaluation.total} ({evaluation.recommendation.value})")
    elif status == S.CANDIDATE and evaluation.recommendation == Recommendation.INDEX:
        change = (S.QUALIFIED, T.QUALIFICATION, f"scored {evaluation.total}")

    if not apply:
        return [Change(collection.key, status, change[0], change[2])] if change else []
    details = evaluation.as_dict() | {"below_bar_since": below_since, "empty_since": empty_since, "duplicates": duplicates}
    collection.qualification = details
    collection.qualification_score = evaluation.total
    collection.last_evaluated_at = now
    if change is None:
        return []
    new, trigger, reason = change
    job_collections.transition(db, collection, new, reason=reason, trigger=trigger, details=details)
    return [Change(collection.key, status, new, reason)]


def _approved_since(collection: JobCollection, since: str) -> bool:
    """Whether a person put it in the index on or after `since` (an Eastern
    date): their call stands over a score they saw."""
    return any(
        e.trigger == T.MANUAL and e.new_status == S.INDEXABLE and eastern(e.created_at).date().isoformat() >= since
        for e in collection.events
    )


def _days(today: date, since: str) -> int:
    return (today - date.fromisoformat(since)) // timedelta(days=1)
