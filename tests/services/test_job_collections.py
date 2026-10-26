"""Tests for app.services.job_collections — creating collections and the
lifecycle rules. In-memory SQLite, like the other service tests."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import app.models as m
from app.db.base import Base
from app.models.enums import CollectionStatus as S
from app.models.enums import CollectionTrigger as T
from app.models.enums import CollectionType, JobSector, WorkplaceType
from app.services import collection_dimensions as cd
from app.services import job_collections as jc
from app.services.job_collections import CollectionError


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(_type, _compiler, **_kw):
    return "JSON"


ADMIN = uuid.uuid4()
NOW = datetime(2026, 10, 26, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.JobCollection.__table__, m.JobCollectionEvent.__table__])
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


def _new(db, **values):
    return jc.create_collection(db, reason="seed list", trigger=T.SYSTEM, **values)


def _move(db, collection, status, trigger=T.MANUAL, **kw):
    actor = ADMIN if trigger == T.MANUAL else None
    return jc.transition(db, collection, status, reason=f"to {status}", trigger=trigger, actor_user_id=actor, **kw)


# ------------------------------------------------------------- creating


def test_create_records_a_candidate_and_its_first_event(db):
    c = _new(db, role="software-engineer", location="raleigh-nc")
    db.commit()
    assert c.status == S.CANDIDATE
    assert c.collection_type == CollectionType.ROLE_LOCATION
    assert c.key == "role=software-engineer;location=raleigh-nc"
    [event] = c.events
    assert (event.previous_status, event.new_status, event.trigger, event.reason) == (None, S.CANDIDATE, T.SYSTEM, "seed list")


@pytest.mark.parametrize(
    "values,expected",
    [
        ({"category": "healthcare"}, CollectionType.CATEGORY),
        ({"role": "software-engineer"}, CollectionType.ROLE),
        ({"location": "remote"}, CollectionType.LOCATION),
        ({"location": "north-carolina"}, CollectionType.LOCATION),
        ({"category": "healthcare", "location": "raleigh-nc"}, CollectionType.CATEGORY_LOCATION),
        ({"category": "healthcare", "location": "remote"}, CollectionType.CATEGORY_REMOTE),
        ({"role": "software-engineer", "location": "remote"}, CollectionType.ROLE_REMOTE),
        ({"role": "software-engineer", "salary": "150k-plus"}, CollectionType.ROLE_SALARY),
        ({"category": "sales", "salary": "100k-plus"}, CollectionType.CATEGORY_SALARY),
        ({"role": "software-engineer", "freshness": "posted-today"}, CollectionType.ROLE_FRESHNESS),
        ({"category": "sales", "freshness": "found-this-week"}, CollectionType.CATEGORY_FRESHNESS),
    ],
)
def test_collection_type_follows_the_dimensions(db, values, expected):
    assert _new(db, **values).collection_type == expected


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"salary": "100k-plus"},  # a salary alone isn't a collection
        {"role": "software-engineer", "category": "engineering-tech"},
        {"role": "software-engineer", "location": "raleigh-nc", "salary": "100k-plus"},  # spec §10: no arbitrary combinations
    ],
)
def test_combinations_outside_the_types_are_refused(db, values):
    with pytest.raises(CollectionError, match="no collection type"):
        _new(db, **values)


def test_unknown_slugs_and_dimensions_are_refused(db):
    with pytest.raises(CollectionError, match="unknown role 'barista'"):
        _new(db, role="barista")
    with pytest.raises(CollectionError, match="unknown dimension technology"):
        _new(db, technology="python")


def test_one_collection_per_combination(db):
    _new(db, category="healthcare", location="remote")
    with pytest.raises(CollectionError, match="already exists"):
        _new(db, location="remote", category="healthcare")


def test_dimensions_match_the_collections_jobs(db):
    c = _new(db, category="healthcare", location="remote")
    dims = jc.dimensions(c)
    assert [d.kind for d in dims] == ["category", "location"]

    class Job:
        sector = JobSector.HEALTHCARE
        workplace_type = WorkplaceType.REMOTE

    assert cd.matches_all(Job(), dims, NOW)


# ------------------------------------------------------------ lifecycle


def test_the_full_path_to_the_index_and_back_out(db):
    c = _new(db, role="software-engineer")
    _move(db, c, S.QUALIFIED, T.QUALIFICATION, details={"score": 81})
    _move(db, c, S.PUBLISHED)
    assert not c.is_indexable
    _move(db, c, S.INDEXABLE)
    assert c.is_indexable
    _move(db, c, S.DECLINING, T.LIFECYCLE)
    assert c.is_indexable
    _move(db, c, S.NOINDEX, T.LIFECYCLE)
    assert not c.is_indexable
    _move(db, c, S.ARCHIVED, T.LIFECYCLE)
    db.commit()

    history = db.scalars(select(m.JobCollectionEvent.new_status).order_by(m.JobCollectionEvent.created_at)).all()
    assert len(history) == 7
    assert [e.new_status for e in c.events][-1] == S.ARCHIVED
    qualified = next(e for e in c.events if e.new_status == S.QUALIFIED)
    assert (qualified.previous_status, qualified.details) == (S.CANDIDATE, {"score": 81})


@pytest.mark.parametrize(
    "path,illegal",
    [
        ([], S.PUBLISHED),  # straight from candidate to live
        ([], S.INDEXABLE),
        ([S.QUALIFIED], S.INDEXABLE),  # into the index without being published first
        ([S.ARCHIVED], S.PUBLISHED),  # an archived one starts over as a candidate
    ],
)
def test_illegal_transitions_are_refused_and_change_nothing(db, path, illegal):
    c = _new(db, role="software-engineer")
    for status in path:
        _move(db, c, status, T.QUALIFICATION if status == S.QUALIFIED else T.MANUAL)
    before = (c.status, len(db.scalars(select(m.JobCollectionEvent)).all()))
    with pytest.raises(CollectionError, match="can't go from"):
        _move(db, c, illegal)
    assert (c.status, len(db.scalars(select(m.JobCollectionEvent)).all())) == before


@pytest.mark.parametrize("trigger", [T.QUALIFICATION, T.LIFECYCLE, T.SYSTEM])
def test_only_a_person_publishes_or_indexes(db, trigger):
    c = _new(db, role="software-engineer")
    _move(db, c, S.QUALIFIED, T.QUALIFICATION)
    with pytest.raises(CollectionError, match="only a person"):
        _move(db, c, S.PUBLISHED, trigger)
    assert c.status == S.QUALIFIED


def test_a_change_needs_a_reason_and_a_manual_one_its_admin(db):
    c = _new(db, role="software-engineer")
    with pytest.raises(CollectionError, match="needs a reason"):
        jc.transition(db, c, S.QUALIFIED, reason="  ", trigger=T.QUALIFICATION)
    with pytest.raises(CollectionError, match="needs the admin"):
        jc.transition(db, c, S.ARCHIVED, reason="not useful", trigger=T.MANUAL)
    event = jc.transition(db, c, S.ARCHIVED, reason="not useful", trigger=T.MANUAL, actor_user_id=ADMIN)
    assert event.actor_user_id == ADMIN


def test_every_status_has_its_transitions():
    assert set(jc.TRANSITIONS) == set(S)
    assert all(targets <= set(S) for targets in jc.TRANSITIONS.values())
    # every status is reachable from a new candidate
    reached, frontier = {S.CANDIDATE}, [S.CANDIDATE]
    while frontier:
        for nxt in jc.TRANSITIONS[frontier.pop()] - reached:
            reached.add(nxt)
            frontier.append(nxt)
    assert reached == set(S)


def test_events_keep_their_order_within_one_transaction(db):
    c = _new(db, role="software-engineer")
    _move(db, c, S.QUALIFIED, T.QUALIFICATION)
    _move(db, c, S.ARCHIVED)
    _move(db, c, S.CANDIDATE)
    db.commit()
    db.expire_all()
    stamps = [e.created_at for e in c.events]
    assert len(set(stamps)) == 4
    assert [e.new_status for e in c.events] == [S.CANDIDATE, S.QUALIFIED, S.ARCHIVED, S.CANDIDATE]


def test_deleting_a_collection_deletes_its_history(db):
    c = _new(db, role="software-engineer")
    _move(db, c, S.QUALIFIED, T.QUALIFICATION)
    db.commit()
    assert len(c.events) == 2  # loaded: the ORM, not just the FK, has to handle them
    db.delete(c)
    db.commit()
    assert db.scalars(select(m.JobCollectionEvent)).all() == []
