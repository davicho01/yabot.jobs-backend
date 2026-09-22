import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import app.models as m
from app.api.routes.saved_searches import create_saved_search, delete_saved_search, list_saved_searches
from app.core.config import settings
from app.models.user import User
from app.schemas.saved_search import SavedSearchCreate


def _user() -> User:
    # Never persisted — these routes only ever read current_user.id, so a
    # detached, unsaved instance (real id, nothing else touched) is enough.
    return User(id=uuid.uuid4(), email=f"{uuid.uuid4()}@example.com")


def test_create_saved_search_stores_the_given_filters(db):
    user = _user()
    payload = SavedSearchCreate(name="Remote frontend", q="frontend", workplace_type="remote", salary_min=100000)

    created = create_saved_search(payload, current_user=user, db=db)

    assert created.user_id == user.id
    assert (created.name, created.q, created.workplace_type, created.salary_min) == (
        "Remote frontend",
        "frontend",
        "remote",
        100000,
    )
    assert created.last_alerted_at is None  # never swept yet


def test_list_saved_searches_is_scoped_to_the_current_user_newest_first(db):
    # created_at is a server_default (now()) — SQLite's CURRENT_TIMESTAMP is
    # only second-resolution, so two rows made back-to-back can tie. Setting
    # it explicitly here, rather than via create_saved_search, keeps the
    # ordering assertion below deterministic instead of occasionally flaky.
    mine = _user()
    someone_else = _user()
    now = datetime.now(timezone.utc)
    first = m.SavedSearch(user_id=mine.id, name="First", created_at=now - timedelta(minutes=1))
    second = m.SavedSearch(user_id=mine.id, name="Second", created_at=now)
    not_mine = m.SavedSearch(user_id=someone_else.id, name="Not mine", created_at=now)
    db.add_all([first, second, not_mine])
    db.commit()

    results = list_saved_searches(current_user=mine, db=db)

    assert [r.name for r in results] == ["Second", "First"]


def test_create_saved_search_is_capped_per_user(db):
    user = _user()
    for n in range(settings.saved_search_max_per_user):
        create_saved_search(SavedSearchCreate(name=f"Search {n}"), current_user=user, db=db)

    with pytest.raises(HTTPException) as exc_info:
        create_saved_search(SavedSearchCreate(name="One too many"), current_user=user, db=db)
    assert exc_info.value.status_code == 400

    # The cap is per-user: someone else can still save one of their own.
    other_user = _user()
    create_saved_search(SavedSearchCreate(name="Fine"), current_user=other_user, db=db)


def test_delete_saved_search_only_removes_the_owning_users_row(db):
    owner = _user()
    saved = create_saved_search(SavedSearchCreate(name="Mine"), current_user=owner, db=db)

    other_user = _user()
    with pytest.raises(HTTPException) as exc_info:
        delete_saved_search(saved.id, current_user=other_user, db=db)
    assert exc_info.value.status_code == 404

    delete_saved_search(saved.id, current_user=owner, db=db)
    # A real request commits (see get_db) before anything could re-query in a
    # later request; delete_saved_search itself doesn't flush (matching
    # delete_application's identical pattern) since nothing reads again
    # within the same request — mirror that boundary here rather than in the
    # route.
    db.commit()
    assert list_saved_searches(current_user=owner, db=db) == []


def test_delete_saved_search_404s_for_an_unknown_id(db):
    with pytest.raises(HTTPException) as exc_info:
        delete_saved_search(uuid.uuid4(), current_user=_user(), db=db)
    assert exc_info.value.status_code == 404
