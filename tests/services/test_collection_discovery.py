"""Tests for app.services.collection_discovery: which groups of open jobs
are proposed as collections (spec §15), and what gets recorded."""

from datetime import timedelta

from sqlalchemy import select

import app.models as m
from app.models.enums import CollectionStatus as S
from app.models.enums import WorkplaceType
from app.services import collection_discovery as cdisc
from app.services import geo
from app.services.collection_qualification import Recommendation
from tests.services.test_static_collection_pages import NOW, SLC, collection, db, job  # noqa: F401  (fixtures)


def _keys(proposals):
    return {p.key for p in proposals}


def _live(onsite=60, remote=25):
    return [job() for _ in range(onsite)] + [job(workplace_type=WorkplaceType.REMOTE) for _ in range(remote)]


def test_proposes_role_remote_and_area_groups():
    slc = geo.metro_by_code(SLC).slug
    assert _keys(cdisc.propose(_live(), NOW)) == {
        "role=software-engineer",
        "role=software-engineer;location=remote",
        f"role=software-engineer;location={slc}",
        "role=software-engineer;location=utah",
    }


def test_groups_too_small_to_qualify_are_left_out():
    keys = _keys(cdisc.propose(_live(remote=5), NOW))
    assert "role=software-engineer;location=remote" not in keys
    assert "role=software-engineer" in keys


def test_titles_without_a_role_page_are_ignored():
    live = [job(standard_titles=("Not A Standard Title",)) for _ in range(40)]
    assert cdisc.propose(live, NOW) == []


def test_best_first():
    totals = [p.evaluation.total for p in cdisc.propose(_live(), NOW)]
    assert totals == sorted(totals, reverse=True)


def test_a_dry_run_records_nothing(db):
    found = cdisc.discover(db, _live(), NOW, apply=False)
    assert found
    assert db.scalars(select(m.JobCollection)).all() == []


def test_records_new_collections_for_approval(db):
    found = cdisc.discover(db, _live(), NOW, apply=True)
    recorded = {c.key: c for c in db.scalars(select(m.JobCollection))}
    assert set(recorded) == _keys(found)
    for proposal in found:
        c = recorded[proposal.key]
        expected = S.QUALIFIED if proposal.evaluation.recommendation == Recommendation.INDEX else S.CANDIDATE
        assert c.status == expected
        assert c.qualification_score == proposal.evaluation.total
        assert c.last_evaluated_at is not None
    assert all(p.evaluation.recommendation in cdisc.PROPOSED for p in found)


def test_never_publishes(db):
    cdisc.discover(db, _live(), NOW, apply=True)
    assert {c.status for c in db.scalars(select(m.JobCollection))} <= {S.CANDIDATE, S.QUALIFIED}


def test_existing_and_archived_collections_are_not_proposed_again(db):
    collection(db, S.INDEXABLE, role="software-engineer")
    collection(db, S.ARCHIVED, role="software-engineer", location="remote")
    keys = _keys(cdisc.discover(db, _live(), NOW, apply=True))
    assert "role=software-engineer" not in keys
    assert "role=software-engineer;location=remote" not in keys
    assert cdisc.discover(db, _live(), NOW + timedelta(days=1), apply=True) == []  # all recorded now


def test_at_most_limit_a_run(db):
    found = cdisc.discover(db, _live(), NOW, apply=True, limit=2)
    assert len(found) == 2
    assert len(db.scalars(select(m.JobCollection)).all()) == 2
