"""Tests for app.services.static_collection_pages — which collections get a
page, where, what it says (spec §56) and that its paths can't collide with
the other pages under /jobs/us/ (§57) — and one_off.publish_collection_batch,
which approves a batch of them."""

import itertools
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import app.models as m
from app.db.base import Base
from app.models.enums import CollectionStatus as S
from app.models.enums import CollectionTrigger as T
from app.models.enums import JobSector, ScanStatus, UserRole, WorkplaceType
from app.services import collection_dimensions as cd
from app.services import geo, job_collections, page_store
from app.services import static_collection_pages as scp
from app.services import static_job_pages as sjp
from app.services.page_store import LocalDirPageStore
from app.services.static_pages import SECTORS_BY_SLUG
from one_off import publish_collection_batch


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(_type, _compiler, **_kw):
    return "JSON"


NOW = datetime(2026, 10, 26, 18, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 26)
SLC = "41620"
ADMIN = uuid.uuid4()
counter = itertools.count()


def job(**kw) -> sjp.LinkJob:
    n = next(counter)
    base = dict(
        url_id=f"job-{n}", title=f"Engineer {n}", company_name=f"Company {n % 30}", domain=f"company{n % 30}.com",
        company_key=f"company {n % 30}", title_key=None, sector=JobSector.ENGINEERING_TECH, metros=(SLC, "UT"),
        location="Salt Lake City, UT", workplace_type=WorkplaceType.ONSITE, posted_at=TODAY - timedelta(days=n % 20),
        found_at=NOW - timedelta(days=n % 25, hours=1), scanned="2026-10-24T00:00:00+00:00",
        standard_titles=("Software Engineer",), salary_min=100_000, salary_max=150_000,
        last_seen_at=NOW - timedelta(hours=2), path=f"jobs/us/engineering-tech/2026-10-24/engineer-{n}",
    )
    base.update(kw)
    return sjp.LinkJob(**base)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        m.User.__table__, m.JobCollection.__table__, m.JobCollectionEvent.__table__,
        m.JobPostingUrl.__table__, m.JobPosting.__table__,
    ])
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(scp.settings, "job_collections_enabled", True)


@pytest.fixture
def store(tmp_path, monkeypatch):
    local = LocalDirPageStore(tmp_path)
    monkeypatch.setattr(page_store, "_store", local)
    return local


def collection(db, status: S, **values) -> m.JobCollection:
    c = job_collections.create_collection(db, reason="test", trigger=T.SYSTEM, **values)
    c.status = status  # straight there: the lifecycle has its own tests
    db.flush()
    return c


# ---------------------------------------------------------------- paths


def test_paths():
    assert scp.collection_path(m.JobCollection(role="software-engineer")) == "jobs/us/software-engineer"
    assert scp.collection_path(m.JobCollection(role="software-engineer", location="remote")) == "jobs/us/software-engineer/remote"
    assert scp.collection_path(m.JobCollection(role="software-engineer", location="raleigh-nc")) == "jobs/us/software-engineer/raleigh-nc"
    assert scp.page_path("jobs/us/software-engineer", 1) == "jobs/us/software-engineer"
    assert scp.page_path("jobs/us/software-engineer", 3) == "jobs/us/software-engineer/page/3"


def test_role_paths_never_collide_with_other_pages():
    roles = set(cd.role_slugs())
    # First segment under /jobs/us/: sector pages (and their day and job
    # pages), the hubs and the bucket's own folders own theirs.
    assert not roles & set(SECTORS_BY_SLUG)
    assert not roles & scp.RESERVED_SEGMENTS
    assert all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug) for slug in roles)
    # Second segment: a location slug, "remote", or "page".
    assert geo.metro_by_slug(scp.PAGE_SEGMENT) is None and scp.PAGE_SEGMENT != cd.REMOTE_SLUG


# ---------------------------------------------------------------- planning


def test_nothing_while_the_flag_is_off(db):
    collection(db, S.INDEXABLE, role="software-engineer")
    assert scp.plan_collection_pages(db, [job()], NOW) == []


def test_live_statuses_get_pages(db, enabled):
    collection(db, S.INDEXABLE, role="software-engineer")
    collection(db, S.NOINDEX, role="data-engineer")
    collection(db, S.CANDIDATE, role="backend-engineer")
    collection(db, S.ARCHIVED, role="frontend-engineer")
    live = [job()] + [job(standard_titles=(t,)) for t in ("Data Engineer", "Backend Engineer", "Frontend Engineer")]
    pages = {p.path: p for p in scp.plan_collection_pages(db, live, NOW)}
    assert set(pages) == {"jobs/us/software-engineer", "jobs/us/data-engineer"}
    assert pages["jobs/us/software-engineer"].indexable and not pages["jobs/us/data-engineer"].indexable


def test_members_are_the_collections_open_jobs(db, enabled):
    collection(db, S.INDEXABLE, role="software-engineer", location="remote")
    collection(db, S.INDEXABLE, role="software-engineer", location=geo.metro_by_code(SLC).slug)
    live = [
        job(workplace_type=WorkplaceType.REMOTE, metros=()),
        job(workplace_type=WorkplaceType.REMOTE, metros=(), closed="2026-10-25T00:00:00+00:00"),
        job(),
        job(metros=("12420", "TX")),
        job(standard_titles=("Data Engineer",), workplace_type=WorkplaceType.REMOTE),
    ]
    pages = {p.path: p for p in scp.plan_collection_pages(db, live, NOW)}
    assert pages["jobs/us/software-engineer/remote"].stats.open_jobs == 1
    assert pages["jobs/us/software-engineer/salt-lake-city-ut"].stats.open_jobs == 1


def test_a_collection_with_no_open_jobs_has_no_page(db, enabled):
    collection(db, S.INDEXABLE, role="software-engineer")
    assert scp.plan_collection_pages(db, [job(standard_titles=("Data Engineer",))], NOW) == []


def test_related_links_go_to_live_pages_only(db, enabled):
    from app.services import standard_titles

    related = [t for t in standard_titles.related(["Software Engineer"]) if t != "Software Engineer"]
    assert related, "Software Engineer has related titles"
    other = cd.get("role", sjp.slugify(related[0]))
    collection(db, S.INDEXABLE, role="software-engineer")
    collection(db, S.INDEXABLE, role=other.slug)
    pages = {p.path: p for p in scp.plan_collection_pages(db, [job(), job(standard_titles=(other.name,))], NOW)}
    assert [label for label, _ in pages["jobs/us/software-engineer"].related] == [f"{other.name} jobs"]


# ---------------------------------------------------------------- rendering


def _pages(db, n_jobs: int, status=S.INDEXABLE):
    collection(db, status, role="software-engineer")
    [page] = scp.plan_collection_pages(db, [job() for _ in range(n_jobs)], NOW)
    return page


def test_page_one_seo(db, enabled):
    page = _pages(db, 120)
    html = scp.render_collection_page(page, 1)
    assert '<link rel="canonical" href="https://yabot.jobs/jobs/us/software-engineer" />' in html
    assert '<meta name="robots" content="index, follow" />' in html
    assert "<title>Software Engineer jobs (120 open) | Yabot Jobs</title>" in html
    assert "<h1>Software Engineer jobs</h1>" in html
    assert 'name="description" content="120 open Software Engineer jobs in the US from 30 companies' in html
    assert html.count('class="job-rows__title"') == scp.PER_PAGE  # server-rendered rows
    assert 'rel="next" href="https://yabot.jobs/jobs/us/software-engineer/page/2"' in html
    assert 'rel="prev"' not in html
    assert "$100,000–$150,000" in html and "How we count" in html
    assert '"@type": "BreadcrumbList"' in html


def test_later_pages_canonical_to_themselves(db, enabled):
    page = _pages(db, 120)
    assert page.paths() == ["jobs/us/software-engineer", "jobs/us/software-engineer/page/2", "jobs/us/software-engineer/page/3"]
    html = scp.render_collection_page(page, 3)
    assert '<link rel="canonical" href="https://yabot.jobs/jobs/us/software-engineer/page/3" />' in html
    assert "– Page 3 | Yabot Jobs</title>" in html
    assert html.count('class="job-rows__title"') == 20
    assert 'rel="prev" href="https://yabot.jobs/jobs/us/software-engineer/page/2"' in html and '<link rel="next"' not in html


def test_pages_stop_at_max_pages(db, enabled):
    page = _pages(db, scp.PER_PAGE * scp.MAX_PAGES + 7)
    assert page.page_count == scp.MAX_PAGES
    assert "All on the board" in scp.render_collection_page(page, scp.MAX_PAGES)


def test_noindex_collections_say_so(db, enabled):
    html = scp.render_collection_page(_pages(db, 5, S.NOINDEX), 1)
    assert '<meta name="robots" content="noindex, follow" />' in html


# ---------------------------------------------------------------- publishing


def test_publish_and_retire(db, enabled, store):
    c = collection(db, S.INDEXABLE, role="software-engineer")
    live = [job() for _ in range(60)]
    with ThreadPoolExecutor(2) as pool:
        paths = scp.publish_collection_pages(scp.plan_collection_pages(db, live, NOW), pool)
        assert paths == ["jobs/us/software-engineer", "jobs/us/software-engineer/page/2"]
        assert "Software Engineer jobs" in store.get("jobs/us/software-engineer").decode()

        c.status = S.ARCHIVED
        db.flush()
        paths = scp.publish_collection_pages(scp.plan_collection_pages(db, live, NOW), pool)
    assert paths == ["jobs/us/software-engineer", "jobs/us/software-engineer/page/2"]  # to invalidate
    gone = store.get("jobs/us/software-engineer/page/2").decode()
    assert "noindex, follow" in gone and "No current jobs" in gone
    assert page_store.get_page_store().get(scp.COLLECTION_MANIFEST_KEY) == b'{"paths": []}'


def test_publishing_with_nothing_writes_nothing(store):
    with ThreadPoolExecutor(1) as pool:
        assert scp.publish_collection_pages([], pool) == []
    assert store.get(scp.COLLECTION_MANIFEST_KEY) is None


# ------------------------------------------------------- approving a batch


def _store_jobs(db, jobs):
    for n, j in enumerate(jobs):
        url = m.JobPostingUrl(url=f"https://{j.domain}/{n}", normalized_url=f"https://{j.domain}/{n}", url_hash=f"h{n}",
                              domain=j.domain, created_at=j.found_at, last_seen_at=j.last_seen_at)
        db.add(url)
        db.flush()
        db.add(m.JobPosting(url_id=url.id, title=j.title, company_key=j.company_key, sector=j.sector,
                            standard_titles=list(j.standard_titles), metros=list(j.metros), workplace_type=j.workplace_type,
                            salary_min=j.salary_min, salary_max=j.salary_max, posted_at=j.posted_at, country="US",
                            extraction_status=ScanStatus.SUCCESS))
    db.commit()


def test_a_batch_publishes_what_scores_index(db):
    admin = m.User(email="admin@example.com", role=UserRole.ADMIN)
    db.add(admin)
    _store_jobs(db, [job() for _ in range(120)] + [job(standard_titles=("Data Engineer",)) for _ in range(3)])

    dry = publish_collection_batch.run(db, kinds=["role"], admin=admin, reason="first batch", apply=False, now=NOW)
    assert {key: action for key, _, _, action in dry} == {
        "role=software-engineer": "published and indexable", "role=data-engineer": "recorded as a candidate",
    }
    assert db.scalars(select(m.JobCollection)).all() == []  # a dry run writes nothing

    publish_collection_batch.run(db, kinds=["role"], admin=admin, reason="first batch", apply=True, now=NOW)
    published = db.scalar(select(m.JobCollection).where(m.JobCollection.key == "role=software-engineer"))
    assert published.status == S.INDEXABLE and published.qualification["recommendation"] == "index"
    history = [(e.new_status, e.trigger, e.actor_user_id) for e in published.events]
    assert history == [
        (S.CANDIDATE, T.SYSTEM, None), (S.QUALIFIED, T.QUALIFICATION, None),
        (S.PUBLISHED, T.MANUAL, admin.id), (S.INDEXABLE, T.MANUAL, admin.id),
    ]
    assert published.events[-1].reason == "first batch"
    small = db.scalar(select(m.JobCollection).where(m.JobCollection.key == "role=data-engineer"))
    assert small.status == S.CANDIDATE and small.qualification_score is not None

    again = publish_collection_batch.run(db, kinds=["role"], admin=admin, reason="again", apply=True, now=NOW)
    assert all(action.startswith("exists") for *_, action in again)
    assert len(published.events) == 4
