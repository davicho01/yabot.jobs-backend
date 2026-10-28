"""Share attribution (spec §29–30, PR-19): who shared a job link, and where
a new user first came from.

A share id is random, made in the browser at the tap and carried on the
link as ?ref=share&sid=<id>. Someone signed in who shares from the app
records it (record_share), so a sign-up it brings can be credited to them.
Right after signing up, the app sends what it kept from the first visit —
the share id, or the campaign tags and the first page seen — and it's
recorded once (record_attribution): first touch, never overwritten.

Nothing here identifies one person to another: the id on a link is random,
and only aggregate counts are meant to be read off it.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.job_share import JobShare
from app.models.job_url import JobPostingUrl
from app.models.user import User

SHARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,32}$")
# How long after the account was made its first-visit details are taken:
# the app sends them right after sign-up; a later call is something else.
ATTRIBUTION_WINDOW = timedelta(days=1)


class ShareError(Exception):
    pass


def record_share(db: Session, user: User, share_id: str, job_url_id: uuid.UUID, method: str) -> JobShare:
    """The share, recorded once (sending it again is fine). ShareError for a
    malformed id, an unknown job, or an id someone else already recorded."""
    if not SHARE_ID_RE.match(share_id):
        raise ShareError("malformed share id")
    if db.get(JobPostingUrl, job_url_id) is None:
        raise ShareError("unknown job")
    share = db.get(JobShare, share_id)
    if share is not None:
        if share.user_id != user.id or share.job_url_id != job_url_id:
            raise ShareError("share id already used")
        return share
    share = JobShare(id=share_id, job_url_id=job_url_id, user_id=user.id, method=method)
    db.add(share)
    db.commit()
    return share


def record_attribution(
    db: Session,
    user: User,
    now: datetime,
    *,
    share_id: str | None,
    source: str | None,
    medium: str | None,
    campaign: str | None,
    landing_page: str | None,
) -> bool:
    """Record where `user` first came from, if not yet recorded and the
    account is new; whether it was. A share id the user made themselves
    isn't kept: sharing with yourself isn't a referral."""
    created = user.created_at if user.created_at.tzinfo else user.created_at.replace(tzinfo=timezone.utc)
    if user.attributed_at is not None or now - created > ATTRIBUTION_WINDOW:
        return False
    if share_id is not None and not SHARE_ID_RE.match(share_id):
        share_id = None
    if share_id is not None and (share := db.get(JobShare, share_id)) is not None and share.user_id == user.id:
        share_id = None
    user.signup_share_id = share_id
    user.signup_source = _clip(source, 100)
    user.signup_medium = _clip(medium, 100)
    user.signup_campaign = _clip(campaign, 200)
    user.signup_landing_page = _clip(landing_page, 500)
    user.attributed_at = now
    db.commit()
    return True


def _clip(value: str | None, size: int) -> str | None:
    value = (value or "").strip()
    return value[:size] or None
