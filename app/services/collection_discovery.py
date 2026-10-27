"""Finds new job collections worth a page (spec §15) and records them for a
person to approve. Deterministic and bounded: it groups the open jobs once
by the combinations that have a page (static_collection_pages.RENDERED_TYPES)
— no Cartesian product of dimensions — and keeps only groups with enough
jobs to qualify.

  role           a standard title with open jobs
  role_remote    ... its remote jobs
  role_location  ... its jobs in one metro, micro area or state

Each group is scored (collection_qualification). One that would score INDEX
or REVIEW and isn't in the registry yet is recorded with its score — as
QUALIFIED when it scores INDEX, else as a CANDIDATE, as the approval
batches do — so it shows under "Waiting" on the admin page; at most
MAX_NEW_PER_RUN a run, best first. Nothing is published:
only a person does that (job_collections.MANUAL_ONLY). A collection already
in the registry, archived ones included, is never proposed again.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.enums import CollectionStatus as S
from app.models.enums import CollectionTrigger as T
from app.models.enums import WorkplaceType
from app.models.job_collection import JobCollection
from app.services import collection_dimensions, geo, job_collections
from app.services.collection_dimensions import REMOTE_SLUG
from app.services.collection_qualification import THRESHOLDS, Evaluation, Recommendation, evaluate_jobs
from app.services.static_job_pages import LinkJob, slugify

MAX_NEW_PER_RUN = 100
PROPOSED = (Recommendation.INDEX, Recommendation.REVIEW)


@dataclass(frozen=True)
class Proposal:
    values: dict[str, str]  # create_collection's dimension slugs
    evaluation: Evaluation

    @property
    def key(self) -> str:
        return job_collections.collection_key(self.values)


def propose(live: list[LinkJob], now: datetime) -> list[Proposal]:
    """A scored proposal for every role, role + remote and role + area
    group of open jobs with at least THRESHOLDS.min_jobs of them, best
    first."""
    roles = set(collection_dimensions.role_slugs())
    by_role: dict[str, list[LinkJob]] = defaultdict(list)
    by_role_area: dict[tuple[str, str], list[LinkJob]] = defaultdict(list)
    for job in live:
        if job.closed:
            continue
        remote = job.workplace_type == WorkplaceType.REMOTE
        for title in job.standard_titles:
            role = slugify(title)
            if role not in roles:
                continue
            by_role[role].append(job)
            if remote:
                by_role_area[(role, REMOTE_SLUG)].append(job)
            for code in job.metros:
                if (metro := geo.metro_by_code(code)) is not None:
                    by_role_area[(role, metro.slug)].append(job)

    enough = THRESHOLDS.min_jobs
    out = [
        Proposal({"role": role}, evaluate_jobs(jobs, now))
        for role, jobs in by_role.items() if len(jobs) >= enough
    ]
    out += [
        Proposal({"role": role, "location": area}, evaluate_jobs(jobs, now, parent_size=len(by_role[role])))
        for (role, area), jobs in by_role_area.items() if len(jobs) >= enough
    ]
    out.sort(key=lambda p: (-p.evaluation.total, p.key))
    return out


def discover(
    db: Session, live: list[LinkJob], now: datetime, *, apply: bool, limit: int = MAX_NEW_PER_RUN
) -> list[Proposal]:
    """The new collections proposed this run (at most `limit`), recorded
    when `apply`."""
    registry = {c.key: c for c in db.scalars(select(JobCollection).options(selectinload(JobCollection.events)))}
    returning = {key for key, c in registry.items() if _archived_by_lifecycle(c)}
    new = [
        p for p in propose(live, now)
        if p.evaluation.recommendation in PROPOSED and (p.key not in registry or p.key in returning)
    ]
    new = new[:limit]
    if not apply:
        return new
    for proposal in new:
        e = proposal.evaluation
        if proposal.key in returning:
            collection = registry[proposal.key]
            job_collections.transition(
                db, collection, S.CANDIDATE, reason="found by discovery again", trigger=T.LIFECYCLE
            )
        else:
            collection = job_collections.create_collection(
                db, reason="found by discovery", trigger=T.SYSTEM, **proposal.values
            )
        if e.recommendation == Recommendation.INDEX:
            job_collections.transition(
                db, collection, S.QUALIFIED, reason=f"scored {e.total}", trigger=T.QUALIFICATION, details=e.as_dict()
            )
        collection.qualification_score = e.total
        collection.qualification = e.as_dict()
        collection.last_evaluated_at = now
    db.commit()
    return new


def _archived_by_lifecycle(collection: JobCollection) -> bool:
    return collection.status == S.ARCHIVED and bool(collection.events) and collection.events[-1].trigger == T.LIFECYCLE
