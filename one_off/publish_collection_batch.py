"""Approve a batch of job collections in one decision (ADR-013, amended
2026-10-27): score every candidate of the given kinds with the
qualification rules (app.services.collection_qualification) and publish —
straight into the index — the ones that score INDEX. Each is recorded as
its own registry entry with a history: proposed (system), qualified (with
its score), then published and indexable as a manual change by the admin
named here, with the batch's reason. The rest are recorded as candidates
with their score, unpublished.

Pages appear on the next static generator run, once
JOB_COLLECTIONS_ENABLED is on. Re-running is safe: a collection that
already exists keeps its status, and only gets its score refreshed.

Dry run by default (prints what it would do, writes nothing); --apply
writes.

Usage:
    python -m one_off.publish_collection_batch --kind role --admin-email you@example.com \\
        --reason "First batch: tech roles" [--apply]
"""

import argparse
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.enums import CollectionStatus as S
from app.models.enums import CollectionTrigger as T
from app.models.enums import UserRole
from app.models.job_collection import JobCollection
from app.models.user import User
from app.services import job_collections
from app.services.collection_dimensions import REMOTE_SLUG
from app.services.collection_qualification import Recommendation, evaluate_jobs
from app.services.static_job_pages import load_link_index
from one_off.collection_report import candidates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.publish_collection_batch")

KINDS = ("role", "role_remote")


def values_for(kind: str, key: str) -> dict[str, str]:
    if kind == "role":
        return {"role": key}
    role, _ = key.split("/")
    return {"role": role, "location": REMOTE_SLUG}


def run(db, *, kinds: list[str], admin: User, reason: str, apply: bool, now: datetime) -> list[tuple[str, int, str, str]]:
    """[(key, total, recommendation, what happened)], one per candidate."""
    live = [job for job in load_link_index(db, now).values() if not job.closed]
    out = []
    for kind, key, _name, jobs, parent, single in candidates(live):
        if kind not in kinds:
            continue
        evaluation = evaluate_jobs(jobs, now, parent_size=parent, single_employer=single)
        values = values_for(kind, key)
        collection_key = job_collections.collection_key(values)
        collection = db.scalar(select(JobCollection).where(JobCollection.key == collection_key))
        publish = evaluation.recommendation == Recommendation.INDEX
        if collection is not None:
            action = f"exists ({collection.status}), score refreshed"
        elif publish:
            action = "published and indexable"
        else:
            action = "recorded as a candidate"
        out.append((collection_key, evaluation.total, evaluation.recommendation.value, action))
        if not apply:
            continue
        if collection is None:
            collection = job_collections.create_collection(
                db, reason=f"proposed by batch: {reason}", trigger=T.SYSTEM, **values
            )
            if publish:
                details = evaluation.as_dict()
                job_collections.transition(db, collection, S.QUALIFIED, reason=f"scored {evaluation.total}",
                                           trigger=T.QUALIFICATION, details=details)
                for status in (S.PUBLISHED, S.INDEXABLE):
                    job_collections.transition(db, collection, status, reason=reason, trigger=T.MANUAL,
                                               actor_user_id=admin.id)
        collection.qualification_score = evaluation.total
        collection.qualification = evaluation.as_dict()
        collection.last_evaluated_at = now
    if apply:
        db.commit()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kind", action="append", choices=KINDS, required=True)
    parser.add_argument("--admin-email", required=True, help="The admin approving the batch (an ADMIN user).")
    parser.add_argument("--reason", required=True, help="Why this batch is approved; recorded on every collection.")
    parser.add_argument("--apply", action="store_true", help="Write. Without it, a dry run.")
    args = parser.parse_args()

    with SessionLocal() as db:
        admin = db.scalar(select(User).where(func.lower(User.email) == args.admin_email.strip().lower()))
        if admin is None or admin.role != UserRole.ADMIN:
            raise SystemExit(f"{args.admin_email} isn't an admin user.")
        rows = run(db, kinds=args.kind, admin=admin, reason=args.reason, apply=args.apply,
                   now=datetime.now(timezone.utc))
    for key, total, recommendation, action in rows:
        print(f"{key}\t{total}\t{recommendation}\t{action}")
    published = sum(1 for *_, action in rows if action == "published and indexable")
    logger.info("%d candidate(s); %d %s.%s", len(rows), published,
                "published" if args.apply else "would be published", "" if args.apply else " Dry run: nothing written.")


if __name__ == "__main__":
    main()
