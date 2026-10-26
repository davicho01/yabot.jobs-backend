"""The job collection registry: creating collections and moving them
through their lifecycle (app.models.job_collection). Every status change
goes through transition(), which allows only the moves in TRANSITIONS and
records each one as a JobCollectionEvent with its reason and trigger — so
there is a history of why every collection is where it is (spec §11, §45).

Phase 1 rule: a collection goes live (PUBLISHED) or into the index
(INDEXABLE) only on a person's say-so — MANUAL_ONLY. Qualification and
lifecycle automation can propose, demote and retire, not publish.

Like the rest of app.services, these add and flush but don't commit: the
caller does.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import CollectionStatus as S
from app.models.enums import CollectionTrigger, CollectionType
from app.models.job_collection import JobCollection, JobCollectionEvent
from app.services import collection_dimensions
from app.services.collection_dimensions import REMOTE_SLUG, Dimension


class CollectionError(ValueError):
    """An invalid collection or status change; the message says why."""


# The dimension order a collection's key is written in.
DIMENSION_KINDS = ("category", "role", "location", "salary", "freshness")

# Which dimensions each type combines. *_LOCATION excludes remote, which is
# its own type (*_REMOTE).
COLLECTION_DIMENSIONS: dict[CollectionType, frozenset[str]] = {
    CollectionType.CATEGORY: frozenset({"category"}),
    CollectionType.ROLE: frozenset({"role"}),
    CollectionType.LOCATION: frozenset({"location"}),
    CollectionType.CATEGORY_LOCATION: frozenset({"category", "location"}),
    CollectionType.CATEGORY_REMOTE: frozenset({"category", "location"}),
    CollectionType.ROLE_LOCATION: frozenset({"role", "location"}),
    CollectionType.ROLE_REMOTE: frozenset({"role", "location"}),
    CollectionType.CATEGORY_SALARY: frozenset({"category", "salary"}),
    CollectionType.ROLE_SALARY: frozenset({"role", "salary"}),
    CollectionType.CATEGORY_FRESHNESS: frozenset({"category", "freshness"}),
    CollectionType.ROLE_FRESHNESS: frozenset({"role", "freshness"}),
}

TRANSITIONS: dict[S, frozenset[S]] = {
    S.CANDIDATE: frozenset({S.QUALIFIED, S.ARCHIVED}),
    S.QUALIFIED: frozenset({S.PUBLISHED, S.CANDIDATE, S.ARCHIVED}),
    S.PUBLISHED: frozenset({S.INDEXABLE, S.NOINDEX, S.ARCHIVED}),
    S.INDEXABLE: frozenset({S.PERFORMING, S.DECLINING, S.NOINDEX, S.ARCHIVED}),
    S.PERFORMING: frozenset({S.DECLINING, S.NOINDEX, S.ARCHIVED}),
    S.DECLINING: frozenset({S.PERFORMING, S.INDEXABLE, S.NOINDEX, S.ARCHIVED}),
    S.NOINDEX: frozenset({S.INDEXABLE, S.ARCHIVED}),
    S.ARCHIVED: frozenset({S.CANDIDATE}),  # proposed again
}

MANUAL_ONLY = frozenset({S.PUBLISHED, S.INDEXABLE})


def collection_type_for(values: dict[str, str]) -> CollectionType:
    """The type a set of dimension values makes, or CollectionError if it
    isn't one of COLLECTION_DIMENSIONS (spec §10: no arbitrary combinations)."""
    kinds = frozenset(values)
    remote = values.get("location") == REMOTE_SLUG
    for collection_type, wanted in COLLECTION_DIMENSIONS.items():
        if wanted != kinds:
            continue
        if "location" in kinds and len(kinds) > 1 and remote != collection_type.value.endswith("_remote"):
            continue
        return collection_type
    raise CollectionError(f"no collection type combines {', '.join(sorted(kinds)) or 'nothing'}")


def collection_key(values: dict[str, str]) -> str:
    return ";".join(f"{kind}={values[kind]}" for kind in DIMENSION_KINDS if kind in values)


def dimension_values(collection: JobCollection) -> dict[str, str]:
    return {kind: getattr(collection, kind) for kind in DIMENSION_KINDS if getattr(collection, kind) is not None}


def dimensions(collection: JobCollection) -> list[Dimension]:
    """The collection's dimensions, to match jobs against (matches_all /
    clause_all)."""
    return [collection_dimensions.get(kind, slug) for kind, slug in dimension_values(collection).items()]


def create_collection(
    db: Session,
    *,
    reason: str,
    trigger: CollectionTrigger,
    actor_user_id: uuid.UUID | None = None,
    title: str | None = None,
    h1: str | None = None,
    meta_description: str | None = None,
    **values: str | None,
) -> JobCollection:
    """A new CANDIDATE collection from one slug per dimension it uses
    (category=, role=, location=, salary=, freshness=)."""
    unknown = set(values) - set(DIMENSION_KINDS)
    if unknown:
        raise CollectionError(f"unknown dimension {', '.join(sorted(unknown))}")
    values = {kind: slug for kind, slug in values.items() if slug is not None}
    for kind, slug in values.items():
        if collection_dimensions.get(kind, slug) is None:
            raise CollectionError(f"unknown {kind} {slug!r}")
    collection_type = collection_type_for(values)
    key = collection_key(values)
    if db.scalar(select(JobCollection.id).where(JobCollection.key == key)) is not None:
        raise CollectionError(f"collection {key} already exists")
    _check_event(None, S.CANDIDATE, reason, trigger, actor_user_id)

    collection = JobCollection(
        key=key, collection_type=collection_type, status=S.CANDIDATE, title=title, h1=h1,
        meta_description=meta_description, **values,
    )
    db.add(collection)
    db.flush()
    _record(db, collection, None, S.CANDIDATE, reason, trigger, actor_user_id, None)
    return collection


def transition(
    db: Session,
    collection: JobCollection,
    new_status: S,
    *,
    reason: str,
    trigger: CollectionTrigger,
    actor_user_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> JobCollectionEvent:
    """Move `collection` to `new_status` if TRANSITIONS and MANUAL_ONLY allow
    it, and record why; CollectionError otherwise, changing nothing."""
    previous = S(collection.status)
    if new_status not in TRANSITIONS[previous]:
        raise CollectionError(f"{collection.key}: can't go from {previous} to {new_status}")
    _check_event(previous, new_status, reason, trigger, actor_user_id)
    collection.status = new_status
    return _record(db, collection, previous, new_status, reason, trigger, actor_user_id, details)


def _check_event(
    previous: S | None, new_status: S, reason: str, trigger: CollectionTrigger, actor_user_id: uuid.UUID | None
) -> None:
    if not reason or not reason.strip():
        raise CollectionError("a status change needs a reason")
    if trigger == CollectionTrigger.MANUAL and actor_user_id is None:
        raise CollectionError("a manual change needs the admin who made it")
    if new_status in MANUAL_ONLY and trigger != CollectionTrigger.MANUAL:
        raise CollectionError(f"only a person can move a collection to {new_status}")


def _record(
    db: Session,
    collection: JobCollection,
    previous: S | None,
    new_status: S,
    reason: str,
    trigger: CollectionTrigger,
    actor_user_id: uuid.UUID | None,
    details: dict[str, Any] | None,
) -> JobCollectionEvent:
    event = JobCollectionEvent(
        collection_id=collection.id, previous_status=previous, new_status=new_status, trigger=trigger,
        reason=reason.strip(), actor_user_id=actor_user_id, details=details,
    )
    db.add(event)
    db.flush()
    return event
