import uuid
from datetime import datetime, timezone

from app.api.routes.auth import update_current_user
from app.models.user import User
from app.schemas.auth import UserUpdate


def _user(**overrides) -> User:
    # Never persisted (update_current_user is handed current_user directly,
    # never re-queries it) — same reasoning as test_saved_searches.py's _user.
    # id/status/role/created_at are plain Python-side defaults that only
    # apply at actual INSERT time, so — unlike that helper — these have to
    # be filled in by hand: update_current_user's return value round-trips
    # through UserRead, which requires all of them.
    defaults = {
        "id": uuid.uuid4(),
        "email": "person@example.com",
        "display_name": "Ada",
        "status": "active",
        "role": "user",
        "created_at": datetime.now(timezone.utc),
        "email_alerts_enabled": True,
    }
    return User(**{**defaults, **overrides})


def test_update_current_user_only_touches_fields_actually_sent(db):
    user = _user()

    # Sending just email_alerts_enabled must not touch display_name at all —
    # the bug this partial-update logic exists to prevent: unconditionally
    # writing `current_user.display_name = payload.display_name or None`
    # would silently blank it out here, since it's absent from this payload.
    result = update_current_user(UserUpdate(email_alerts_enabled=False), current_user=user, db=db)

    assert result.display_name == "Ada"
    assert result.email_alerts_enabled is False


def test_update_current_user_display_name_still_works_alone(db):
    user = _user()

    result = update_current_user(UserUpdate(display_name="Grace"), current_user=user, db=db)

    assert result.display_name == "Grace"
    assert result.email_alerts_enabled is True  # untouched


def test_update_current_user_display_name_strips_and_blank_clears_it(db):
    user = _user()
    update_current_user(UserUpdate(display_name="  Grace  "), current_user=user, db=db)
    assert user.display_name == "Grace"

    # Explicitly sent as blank — this *is* included in the payload (unlike
    # the omitted-field case above), so it's a deliberate clear.
    result = update_current_user(UserUpdate(display_name=""), current_user=user, db=db)
    assert result.display_name is None


def test_update_current_user_with_neither_field_sent_changes_nothing(db):
    user = _user()

    result = update_current_user(UserUpdate(), current_user=user, db=db)

    assert result.display_name == "Ada"
    assert result.email_alerts_enabled is True
