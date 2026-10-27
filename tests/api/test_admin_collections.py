"""Tests for the admin's job-collection review (app.api.routes.admin_collections,
app.services.collection_admin)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes import admin_collections as routes
from app.db.base import Base
from app.models.enums import CollectionStatus as S
from app.models.enums import CollectionTrigger as T
from app.models.enums import JobSector, UserRole, WorkplaceType
from app.schemas.collection import AdminCollectionAction, AdminCollectionBulkAction
from app.services import collection_admin, job_collections
from app.services import static_collection_pages as scp
from app.services import static_job_pages as sjp


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(_type, _compiler, **_kw):
    return "JSON"


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.User.__table__, m.JobCollection.__table__, m.JobCollectionEvent.__table__])
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture
def admin(db) -> m.User:
    user = m.User(id=uuid.uuid4(), email="admin@example.com", role=UserRole.ADMIN)
    db.add(user)
    db.commit()
    return user


def collection(db, status=S.CANDIDATE, score=None, issues=(), **values) -> m.JobCollection:
    values = values or {"role": "software-engineer"}
    c = job_collections.create_collection(db, reason="proposed", trigger=T.SYSTEM, **values)
    c.status = status
    c.qualification_score = score
    c.qualification = (
        {"recommendation": "review", "issues": list(issues), "stats": {"open_jobs": 40, "companies_hiring": 9}}
        if score is not None else None
    )
    db.commit()
    return c


def act(db, admin, c, action, reason="looks good"):
    return routes.act_on_collection(c.id, AdminCollectionAction(action=action, reason=reason), admin=admin, db=db)


# ---------------------------------------------------------------- actions


@pytest.mark.parametrize("status, actions", [
    (S.CANDIDATE, ["approve", "archive"]),
    (S.QUALIFIED, ["approve", "archive"]),
    (S.PUBLISHED, ["approve", "noindex", "archive"]),
    (S.INDEXABLE, ["noindex", "archive"]),
    (S.NOINDEX, ["approve", "archive"]),
    (S.ARCHIVED, ["restore"]),
])
def test_available_actions(db, status, actions):
    assert collection_admin.available_actions(collection(db, status)) == actions


def test_approving_a_candidate_takes_it_into_the_index_as_the_admin(db, admin):
    c = collection(db, S.CANDIDATE, score=72)
    out = act(db, admin, c, "approve", "Borderline remote batch")
    assert out.status == S.INDEXABLE and out.indexable
    assert out.url == "https://yabot.jobs/jobs/us/software-engineer"
    assert [(e.new_status, e.trigger, e.actor_email) for e in out.events[:3]] == [
        (S.INDEXABLE, T.MANUAL, "admin@example.com"),
        (S.PUBLISHED, T.MANUAL, "admin@example.com"),
        (S.QUALIFIED, T.MANUAL, "admin@example.com"),
    ]
    assert out.events[0].reason == "Borderline remote batch"
    assert out.events[-1].new_status == S.CANDIDATE  # oldest last


def test_noindex_keeps_the_page_and_archive_takes_it_down(db, admin):
    c = collection(db, S.INDEXABLE)
    out = act(db, admin, c, "noindex")
    assert (out.status, out.indexable, out.url is not None) == (S.NOINDEX, False, True)
    out = act(db, admin, c, "archive")
    assert (out.status, out.url, out.actions) == (S.ARCHIVED, None, ["restore"])
    assert act(db, admin, c, "restore").status == S.CANDIDATE


def test_an_action_that_doesnt_apply_is_refused_and_changes_nothing(db, admin):
    c = collection(db, S.INDEXABLE)
    with pytest.raises(HTTPException) as exc:
        act(db, admin, c, "approve")
    assert exc.value.status_code == 409
    assert c.status == S.INDEXABLE and len(c.events) == 1


def test_a_blank_reason_is_refused(db, admin):
    with pytest.raises(job_collections.CollectionError):
        collection_admin.apply_action(db, collection(db), "approve", admin=admin, reason="   ")


def test_unknown_collection_is_a_404(db, admin):
    with pytest.raises(HTTPException) as exc:
        routes.get_collection(uuid.uuid4(), db=db)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------- bulk


def test_bulk_applies_to_each_and_reports_the_rest(db, admin):
    a = collection(db, S.CANDIDATE, role="software-engineer", location="remote")
    b = collection(db, S.QUALIFIED, role="data-engineer", location="remote")
    live = collection(db, S.INDEXABLE, role="data-scientist")
    missing = uuid.uuid4()
    out = routes.act_on_collections(
        AdminCollectionBulkAction(ids=[a.id, b.id, live.id, missing, a.id], action="approve", reason="Remote batch 2"),
        admin=admin, db=db,
    )
    assert sorted(c.name for c in out.updated) == ["Remote Data Engineer jobs", "Remote Software Engineer jobs"]
    assert all(c.status == S.INDEXABLE for c in out.updated)
    assert {e.id for e in out.errors} == {live.id, missing}


# ---------------------------------------------------------------- list


def test_list_filters_sorts_and_counts(db):
    collection(db, S.CANDIDATE, score=65, issues=["too_few_employers"], role="software-engineer", location="remote")
    collection(db, S.CANDIDATE, score=75, role="data-engineer", location="remote")
    collection(db, S.QUALIFIED, role="ai-engineer")
    collection(db, S.INDEXABLE, score=99, role="data-scientist")

    out = routes.list_collections(view="waiting", collection_type=None, q=None, page=1, page_size=50, db=db)
    assert [c.name for c in out.items] == ["Remote Data Engineer jobs", "Remote Software Engineer jobs", "AI Engineer jobs"]
    assert out.items[1].issues == ["too_few_employers"] and out.items[1].qualification_score == 65
    assert (out.items[1].open_jobs, out.items[1].companies) == (40, 9)
    assert out.total == 3 and out.counts == {"candidate": 2, "qualified": 1, "indexable": 1}

    found = routes.list_collections(view="all", collection_type=None, q="data engineer", page=1, page_size=50, db=db)
    assert [c.key for c in found.items] == ["role=data-engineer;location=remote"]
    live = routes.list_collections(view="live", collection_type=None, q=None, page=1, page_size=50, db=db)
    assert [c.name for c in live.items] == ["Data Scientist jobs"]


def test_names(db):
    assert collection_admin.collection_name(collection(db, role="software-engineer", location="raleigh-nc")).startswith(
        "Software Engineer jobs in "
    )


# ------------------------------------------------------------ re-scoring (PR-14)


def test_list_shows_duplicates_and_dates(db, admin):
    c = collection(db, S.INDEXABLE, score=70)
    c.qualification = c.qualification | {
        "freshness_score": 16.0, "duplicates": ["role=backend-engineer"], "below_bar_since": "2026-10-25",
        "empty_since": None,
    }
    db.commit()
    [item] = routes.list_collections(view="live", collection_type=None, q=None, page=1, page_size=50, db=db).items
    assert item.duplicates == ["role=backend-engineer"]
    assert (item.freshness_score, item.below_bar_since, item.empty_since) == (16.0, "2026-10-25", None)
    assert item.can_reevaluate is True
    assert item.path == "/jobs/us/software-engineer"


def test_reevaluate_rescores_and_returns_the_detail(db, admin, monkeypatch):
    from app.services import collection_lifecycle

    c = collection(db, S.CANDIDATE, score=50)
    calls = []

    def reevaluate_one(_db, target, _now):
        calls.append(target.id)
        target.qualification_score = 88
        return []

    monkeypatch.setattr(collection_lifecycle, "reevaluate_one", reevaluate_one)
    detail = routes.reevaluate_collection(c.id, db=db)
    assert calls == [c.id] and detail.qualification_score == 88


def test_archived_collections_cant_be_reevaluated(db, admin):
    c = collection(db, S.ARCHIVED, score=50)
    with pytest.raises(HTTPException) as exc:
        routes.reevaluate_collection(c.id, db=db)
    assert exc.value.status_code == 409
    [item] = routes.list_collections(view="archived", collection_type=None, q=None, page=1, page_size=50, db=db).items
    assert item.can_reevaluate is False


# ---------------------------------------------------------------- preview


def link_job(n: int, **kw) -> sjp.LinkJob:
    now = datetime.now(timezone.utc)
    base = dict(
        url_id=f"job-{n}", title=f"Backend Engineer {n}", company_name=f"Company {n}", domain=f"company{n}.com",
        company_key=f"company {n}", title_key=None, sector=JobSector.ENGINEERING_TECH, metros=("41620", "UT"),
        location="Salt Lake City, UT", workplace_type=WorkplaceType.ONSITE, posted_at=now.date(),
        found_at=now - timedelta(hours=3), scanned=now.isoformat(), standard_titles=("Software Engineer",),
        salary_min=None, salary_max=None, last_seen_at=now - timedelta(hours=1),
        path=f"jobs/us/engineering-tech/{now.date()}/backend-engineer-{n}",
    )
    base.update(kw)
    return sjp.LinkJob(**base)


def test_preview_renders_the_page_from_its_open_jobs_whatever_its_status(db, monkeypatch):
    c = collection(db, S.CANDIDATE, score=50)
    jobs = [link_job(1), link_job(2, standard_titles=("Data Engineer",))]
    monkeypatch.setattr(scp, "load_link_index", lambda _db, _now, where=None: {j.url_id: j for j in jobs})
    html = routes.preview_collection(c.id, db=db).body.decode()
    assert "<h1>Software Engineer jobs</h1>" in html
    assert "Backend Engineer 1" in html and "Backend Engineer 2" not in html


def test_preview_with_no_open_jobs_is_refused(db, monkeypatch):
    c = collection(db, S.INDEXABLE)
    monkeypatch.setattr(scp, "load_link_index", lambda _db, _now, where=None: {})
    with pytest.raises(HTTPException) as exc:
        routes.preview_collection(c.id, db=db)
    assert exc.value.status_code == 409
