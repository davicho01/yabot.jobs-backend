"""The admin's actions on job collections (PR-11): what a person can do to
a collection from where it is, each as the status changes it takes through
app.services.job_collections, so every one is recorded as a MANUAL event
with the admin and their reason.

  approve   candidate/qualified -> published -> indexable; published or
            noindex -> indexable. Its page goes up, indexed, on the next
            generator run.
  noindex   a live page leaves Google's index but stays up.
  archive   its page comes down on the next run.
  restore   an archived collection is proposed again (candidate).

Scores aren't recomputed here: collection_lifecycle re-scores every
collection nightly, and one on demand (the admin's Re-evaluate).

Adds and flushes; the caller commits.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy.orm import Session

from app.models.enums import CollectionStatus as S
from app.models.enums import CollectionTrigger as T
from app.models.job_collection import JobCollection
from app.models.user import User
from app.services import collection_dimensions, job_collections
from app.services.collection_dimensions import REMOTE_SLUG

Action = Literal["approve", "noindex", "archive", "restore"]
View = Literal["waiting", "live", "noindex", "archived", "all"]

# The admin page's tabs: which statuses each lists (None: all).
VIEWS: dict[str, tuple[S, ...] | None] = {
    "waiting": (S.CANDIDATE, S.QUALIFIED),
    "live": (S.PUBLISHED, S.INDEXABLE, S.PERFORMING, S.DECLINING),
    "noindex": (S.NOINDEX,),
    "archived": (S.ARCHIVED,),
    "all": None,
}

# Each action from each status: the statuses it moves through, in order.
_PATHS: dict[str, dict[S, tuple[S, ...]]] = {
    "approve": {
        S.CANDIDATE: (S.QUALIFIED, S.PUBLISHED, S.INDEXABLE),
        S.QUALIFIED: (S.PUBLISHED, S.INDEXABLE),
        S.PUBLISHED: (S.INDEXABLE,),
        S.NOINDEX: (S.INDEXABLE,),
    },
    "noindex": {status: (S.NOINDEX,) for status in (S.PUBLISHED, S.INDEXABLE, S.PERFORMING, S.DECLINING)},
    "archive": {status: (S.ARCHIVED,) for status in S if status != S.ARCHIVED},
    "restore": {S.ARCHIVED: (S.CANDIDATE,)},
}


def available_actions(collection: JobCollection) -> list[str]:
    status = S(collection.status)
    return [action for action, paths in _PATHS.items() if status in paths]


def apply_action(db: Session, collection: JobCollection, action: str, *, admin: User, reason: str) -> None:
    """CollectionError, changing nothing, if `action` can't be taken from
    where the collection is or the reason is blank."""
    path = _PATHS[action].get(S(collection.status))
    if path is None:
        raise job_collections.CollectionError(f"{collection.key}: can't {action} a {collection.status} collection")
    if not reason.strip():
        raise job_collections.CollectionError("a status change needs a reason")
    for status in path:
        job_collections.transition(db, collection, status, reason=reason, trigger=T.MANUAL, actor_user_id=admin.id)


def collection_name(collection: JobCollection) -> str:
    """What its page is called: "Remote Software Engineer jobs",
    "Software Engineer jobs in Raleigh, NC"; the key for kinds without a
    page yet."""
    if collection.h1:
        return collection.h1
    return role_name(collection.role, collection.location) if collection.role else collection.key


def role_name(role_slug: str, location: str | None = None) -> str:
    """"Remote Software Engineer jobs", "Software Engineer jobs in Raleigh,
    NC", "Software Engineer jobs"; the slugs for an unknown role."""
    role = collection_dimensions.get("role", role_slug)
    if role is None:
        return job_collections.collection_key({"role": role_slug, "location": location} if location else {"role": role_slug})
    if location == REMOTE_SLUG:
        return f"Remote {role.name} jobs"
    if location:
        area = collection_dimensions.get("location", location)
        return f"{role.name} jobs in {area.name if area else location}"
    return f"{role.name} jobs"
