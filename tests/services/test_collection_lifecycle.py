"""Tests for app.services.collection_lifecycle: nightly re-scoring and the
automatic lifecycle rules (spec §11), and discovery proposing again what
the lifecycle archived."""

import uuid
from datetime import timedelta

from sqlalchemy import select

import app.models as m
from app.models.enums import CollectionStatus as S
from app.models.enums import CollectionTrigger as T
from app.models.enums import UserRole, WorkplaceType
from app.services import collection_discovery as cdisc
from app.services import collection_lifecycle as cl
from app.services import job_collections
from tests.services.test_static_collection_pages import NOW, collection, db, job  # noqa: F401  (fixtures)

DAY = timedelta(days=1)


def _strong(n=60):
    return [job() for _ in range(n)]


def _weak():
    """Too few jobs, from one company: scores NOINDEX."""
    return [job(company_name="Solo", company_key="solo", domain="solo.com") for _ in range(3)]


def _night(db, live, now):
    return cl.reevaluate_all(db, live, now, apply=True)


def test_rescoring_records_score_and_dates(db):
    c = collection(db, S.INDEXABLE, role="software-engineer")
    assert _night(db, _strong(), NOW) == []
    q = c.qualification
    assert c.qualification_score == q["total"] and q["recommendation"] == "index"
    assert q["below_bar_since"] is None and q["empty_since"] is None and q["duplicates"] == []
    assert c.last_evaluated_at is not None


def test_an_indexed_page_below_the_bar_is_noindexed_after_the_grace_days(db):
    c = collection(db, S.INDEXABLE, role="software-engineer")
    for day in range(cl.GRACE_DAYS):
        assert _night(db, _weak(), NOW + day * DAY) == []
        assert c.status == S.INDEXABLE
    [change] = _night(db, _weak(), NOW + cl.GRACE_DAYS * DAY)
    assert (change.previous, change.new) == (S.INDEXABLE, S.NOINDEX)
    assert c.status == S.NOINDEX
    assert c.events[-1].trigger == T.LIFECYCLE and "below the bar" in c.events[-1].reason


def test_recovering_resets_the_clock(db):
    c = collection(db, S.INDEXABLE, role="software-engineer")
    _night(db, _weak(), NOW)
    _night(db, _strong(), NOW + DAY)
    assert c.qualification["below_bar_since"] is None
    _night(db, _weak(), NOW + 2 * DAY)
    assert _night(db, _weak(), NOW + 3 * DAY) == []  # only 1 day below this time
    assert c.status == S.INDEXABLE


def test_a_persons_approval_stands_over_the_score(db):
    c = collection(db, S.NOINDEX, role="software-engineer")
    admin = m.User(id=uuid.uuid4(), email="a@example.com", role=UserRole.ADMIN)
    db.add(admin)
    _night(db, _weak(), NOW)
    job_collections.transition(db, c, S.INDEXABLE, reason="keep it", trigger=T.MANUAL, actor_user_id=admin.id)
    db.flush()
    assert _night(db, _weak(), NOW + 5 * DAY) == []
    assert c.status == S.INDEXABLE


def test_no_jobs_for_weeks_archives(db):
    c = collection(db, S.INDEXABLE, role="software-engineer")
    _night(db, [], NOW)
    assert c.qualification["empty_since"] == "2026-10-26"
    [change] = _night(db, [], NOW + cl.EMPTY_DAYS * DAY)
    assert change.new == S.ARCHIVED and c.status == S.ARCHIVED
    assert c.events[-1].trigger == T.LIFECYCLE


def test_waiting_collections_move_with_their_score(db):
    weak = collection(db, S.QUALIFIED, role="software-engineer")
    strong = collection(db, S.CANDIDATE, role="data-engineer")
    live = _weak() + [job(standard_titles=("Data Engineer",)) for _ in range(60)]
    changes = {c.key: c.new for c in _night(db, live, NOW)}
    assert changes == {weak.key: S.CANDIDATE, strong.key: S.QUALIFIED}
    assert weak.events[-1].trigger == strong.events[-1].trigger == T.QUALIFICATION


def test_never_indexes_anything(db):
    collection(db, S.CANDIDATE, role="software-engineer")
    collection(db, S.NOINDEX, role="software-engineer", location="remote")
    for day in range(5):
        _night(db, _strong() + [job(workplace_type=WorkplaceType.REMOTE) for _ in range(60)], NOW + day * DAY)
    statuses = {c.status for c in db.scalars(select(m.JobCollection))}
    assert not statuses & {S.PUBLISHED, S.INDEXABLE}


def test_duplicates_are_recorded_both_ways(db):
    a = collection(db, S.INDEXABLE, role="software-engineer")
    b = collection(db, S.CANDIDATE, role="backend-engineer")
    _night(db, [job(standard_titles=("Software Engineer", "Backend Engineer")) for _ in range(30)], NOW)
    assert a.qualification["duplicates"] == [b.key]
    assert b.qualification["duplicates"] == [a.key]


def test_a_dry_run_changes_nothing(db):
    c = collection(db, S.INDEXABLE, role="software-engineer")
    c.qualification = {"below_bar_since": "2026-10-01"}
    db.flush()
    [change] = cl.reevaluate_all(db, _weak(), NOW, apply=False)
    assert change.new == S.NOINDEX
    assert c.status == S.INDEXABLE and c.qualification == {"below_bar_since": "2026-10-01"}


def test_reevaluate_one_reads_its_role_from_the_database(db, monkeypatch):
    c = collection(db, S.CANDIDATE, role="software-engineer")
    seen = {}

    def load(_db, _now, where=None):
        seen["where"] = where
        return {j.url_id: j for j in _strong()}

    monkeypatch.setattr(cl, "load_link_index", load)
    [change] = cl.reevaluate_one(db, c, NOW)
    assert seen["where"] is not None
    assert change.new == S.QUALIFIED and c.qualification["recommendation"] == "index"


def test_discovery_proposes_again_what_the_lifecycle_archived(db):
    c = collection(db, S.INDEXABLE, role="software-engineer")
    _night(db, [], NOW)
    _night(db, [], NOW + cl.EMPTY_DAYS * DAY)
    assert c.status == S.ARCHIVED
    found = cdisc.discover(db, _strong(), NOW, apply=True)  # the jobs are fresh as of NOW
    assert c.key in {p.key for p in found}
    assert c.status == S.QUALIFIED


def test_discovery_leaves_alone_what_a_person_archived(db):
    c = collection(db, S.INDEXABLE, role="software-engineer")
    admin = m.User(id=uuid.uuid4(), email="a@example.com", role=UserRole.ADMIN)
    db.add(admin)
    job_collections.transition(db, c, S.ARCHIVED, reason="not useful", trigger=T.MANUAL, actor_user_id=admin.id)
    db.flush()
    assert c.key not in {p.key for p in cdisc.discover(db, _strong(), NOW, apply=True)}
    assert c.status == S.ARCHIVED
