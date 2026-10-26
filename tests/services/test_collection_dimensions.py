"""Tests for app.services.collection_dimensions — which jobs each collection
dimension includes, in memory and as SQL (the two must agree). Same
in-memory SQLite setup as test_static_job_pages.py; the JSONB @> filters,
which SQLite can't run, are checked as compiled Postgres."""

import itertools
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import app.models as m
from app.db.base import Base
from app.models.enums import JobSector, ScanStatus, WorkplaceType
from app.services import collection_dimensions as cd
from app.services import geo, standard_titles
from app.services.static_pages import SECTORS_BY_SLUG


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(_type, _compiler, **_kw):
    return "JSON"


NOW = datetime(2026, 10, 26, 18, 0, tzinfo=timezone.utc)  # 2 pm Eastern
TODAY = date(2026, 10, 26)
SLC = "41620"  # Salt Lake City, UT metro


@dataclass
class Job:
    sector: str = JobSector.ENGINEERING_TECH
    standard_titles: list[str] | None = None
    metros: list[str] = field(default_factory=list)
    workplace_type: str = WorkplaceType.ONSITE
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    posted_at: date | None = None
    found_at: datetime = NOW - timedelta(days=3)
    last_seen_at: datetime | None = None


def _in(kind: str, slug: str, **job) -> bool:
    dimension = cd.get(kind, slug)
    assert dimension is not None, (kind, slug)
    return dimension.matches(Job(**job), NOW)


# ------------------------------------------------------------- category


def test_category_is_the_jobs_sector():
    assert _in("category", "healthcare", sector=JobSector.HEALTHCARE)
    assert not _in("category", "healthcare", sector=JobSector.ENGINEERING_TECH)
    assert cd.get("category", "healthcare").name == "Healthcare"


def test_unknown_sector_is_not_a_category():
    assert cd.get("category", "other") is None
    assert cd.get("category", "unknown") is None
    assert not _in("category", "engineering-tech", sector=JobSector.UNKNOWN)


# ----------------------------------------------------------------- role


def test_role_is_an_exact_standard_title_tag():
    assert _in("role", "software-engineer", standard_titles=["Software Engineer", "Backend Engineer"])
    # "Software Developer" is a related title of Software Engineer: title
    # search adds it, a role collection doesn't
    assert not _in("role", "software-engineer", standard_titles=["Software Developer"])
    assert not _in("role", "software-engineer", standard_titles=None)
    assert cd.get("role", "software-engineer").name == "Software Engineer"


def test_every_standard_title_has_its_own_role_slug():
    titles = standard_titles.list_titles()
    assert len(cd.role_slugs()) == len(titles) == 103
    assert all(cd.get("role", slug) is not None for slug in cd.role_slugs())
    assert cd.get("role", "barista") is None


# ------------------------------------------------------------- location


def test_location_metro_and_state():
    utah = geo.metro_by_code("UT")
    slc = geo.metro_by_code(SLC)
    assert _in("location", slc.slug, metros=[SLC, "UT"])
    assert _in("location", utah.slug, metros=[SLC, "UT"])
    assert _in("location", utah.slug, metros=["UT"])  # a state-only entry
    assert not _in("location", slc.slug, metros=["UT"])  # elsewhere in the state
    assert not _in("location", slc.slug, metros=["12420", "TX"])
    assert cd.get("location", slc.slug).name == "Salt Lake City, UT"


def test_location_remote_is_the_workplace_type():
    assert _in("location", "remote", workplace_type=WorkplaceType.REMOTE)
    assert not _in("location", "remote", workplace_type=WorkplaceType.HYBRID)
    assert not _in("location", "remote", workplace_type=WorkplaceType.UNKNOWN)
    # a remote job with an office listed is in both
    assert _in("location", geo.metro_by_code(SLC).slug, workplace_type=WorkplaceType.REMOTE, metros=[SLC, "UT"])


def test_unknown_location():
    assert cd.get("location", "atlantis") is None


# --------------------------------------------------------------- salary


def test_salary_floor_reads_the_low_end_of_the_range():
    assert _in("salary", "100k-plus", salary_min=100_000, salary_max=140_000)  # floor itself counts
    assert not _in("salary", "100k-plus", salary_min=95_000, salary_max=160_000)
    assert _in("salary", "100k-plus", salary_max=120_000)  # only a max listed
    assert not _in("salary", "100k-plus")
    assert cd.get("salary", "100k-plus").name == "$100k+"


def test_salary_is_annual_usd_only():
    assert not _in("salary", "50k-plus", salary_min=45, salary_max=60)  # hourly
    assert not _in("salary", "50k-plus", salary_min=8_000, salary_max=9_000)  # monthly
    assert not _in("salary", "50k-plus", salary_min=90_000, salary_currency="EUR")
    assert _in("salary", "50k-plus", salary_min=90_000, salary_currency="USD")
    assert not _in("salary", "50k-plus", salary_min=150_000_000)  # a parsing error


def test_salary_slugs():
    assert cd.salary_slugs() == ["50k-plus", "75k-plus", "100k-plus", "150k-plus", "200k-plus"]
    assert cd.get("salary", "90k-plus") is None


# ----------------------------------------------- freshness (spec §55)


def test_found_today_but_posted_a_month_ago_is_not_posted_today():
    job = dict(posted_at=TODAY - timedelta(days=30), found_at=NOW - timedelta(hours=2))
    assert _in("freshness", "found-today", **job)
    assert not _in("freshness", "posted-today", **job)
    assert not _in("freshness", "posted-this-week", **job)


def test_posted_today_needs_the_employers_date():
    assert _in("freshness", "posted-today", posted_at=TODAY)
    # no employer date: found today is never posted today
    assert not _in("freshness", "posted-today", posted_at=None, found_at=NOW - timedelta(hours=1))
    assert not _in("freshness", "posted-today", posted_at=TODAY + timedelta(days=1))  # future: bad data


def test_verified_today_is_the_last_crawl_listing():
    assert _in("freshness", "verified-today", last_seen_at=NOW - timedelta(hours=1), found_at=NOW - timedelta(days=40))
    assert not _in("freshness", "verified-today", last_seen_at=NOW - timedelta(days=2))
    assert not _in("freshness", "verified-today", last_seen_at=None, found_at=NOW - timedelta(hours=1))  # never crawled
    # verified today doesn't make it found or posted today
    assert not _in("freshness", "found-today", last_seen_at=NOW, found_at=NOW - timedelta(days=40))


def test_days_are_eastern():
    # 03:00 UTC on the 26th is 11 pm Eastern on the 25th
    late = datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)
    assert not _in("freshness", "found-today", found_at=late)
    assert _in("freshness", "found-today", found_at=datetime(2026, 10, 26, 4, 30, tzinfo=timezone.utc))
    assert _in("freshness", "found-today", found_at=late.replace(tzinfo=None) + timedelta(hours=2))  # SQLite: naive UTC


def test_this_week_is_seven_eastern_days_including_today():
    assert _in("freshness", "posted-this-week", posted_at=TODAY - timedelta(days=6))
    assert not _in("freshness", "posted-this-week", posted_at=TODAY - timedelta(days=7))
    assert _in("freshness", "found-this-week", found_at=NOW - timedelta(days=6))
    assert not _in("freshness", "found-this-week", found_at=NOW - timedelta(days=8))


# ---------------------------------------------------------- combining


def test_matches_all_combines_dimensions():
    dims = [cd.get("role", "software-engineer"), cd.get("location", "remote")]
    assert cd.matches_all(Job(standard_titles=["Software Engineer"], workplace_type=WorkplaceType.REMOTE), dims, NOW)
    assert not cd.matches_all(Job(standard_titles=["Software Engineer"]), dims, NOW)


def test_slugs_are_unique_across_kinds():
    # Metro and state slugs are geo's own (unique, a metro's ends in its state).
    every = list(SECTORS_BY_SLUG) + cd.role_slugs() + cd.salary_slugs() + cd.freshness_slugs() + [cd.REMOTE_SLUG]
    assert len(every) == len(set(every))
    assert not any(geo.metro_by_slug(slug) for slug in every)


def test_unknown_kind():
    assert cd.get("technology", "python") is None


# ------------------------------------------- SQL agrees with in-memory


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.JobPostingUrl.__table__, m.JobPosting.__table__])
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture
def add(db):
    counter = itertools.count()

    def _add(job: Job) -> str:
        n = next(counter)
        url = m.JobPostingUrl(
            url=f"https://example.com/jobs/{n}",
            normalized_url=f"https://example.com/jobs/{n}",
            url_hash=f"hash-{n}",
            domain="example.com",
            created_at=job.found_at,
            last_seen_at=job.last_seen_at,
        )
        db.add(url)
        db.flush()
        db.add(
            m.JobPosting(
                url_id=url.id,
                title=f"Job {n}",
                sector=job.sector,
                standard_titles=job.standard_titles,
                metros=job.metros,
                workplace_type=job.workplace_type,
                salary_min=job.salary_min,
                salary_max=job.salary_max,
                salary_currency=job.salary_currency,
                posted_at=job.posted_at,
                country="US",
                extraction_status=ScanStatus.SUCCESS,
            )
        )
        db.commit()
        return str(url.id)

    return _add


JOBS = [
    Job(),
    Job(sector=JobSector.HEALTHCARE, workplace_type=WorkplaceType.REMOTE),
    Job(salary_min=100_000, salary_max=140_000),
    Job(salary_min=95_000, salary_max=160_000, salary_currency="USD"),
    Job(salary_max=120_000),
    Job(salary_min=45, salary_max=60),
    Job(salary_min=90_000, salary_currency="EUR"),
    Job(salary_min=150_000_000),
    Job(salary_min=210_000, salary_max=260_000),
    Job(posted_at=TODAY),
    Job(posted_at=TODAY - timedelta(days=30), found_at=NOW - timedelta(hours=2)),
    Job(posted_at=TODAY - timedelta(days=6)),
    Job(posted_at=TODAY + timedelta(days=1)),
    Job(found_at=datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)),
    Job(found_at=datetime(2026, 10, 26, 4, 30, tzinfo=timezone.utc)),
    Job(found_at=NOW - timedelta(days=6)),
    Job(last_seen_at=NOW - timedelta(hours=1), found_at=NOW - timedelta(days=40)),
    Job(last_seen_at=NOW - timedelta(days=2)),
]

SQLITE_DIMENSIONS = (
    [("category", slug) for slug in ("engineering-tech", "healthcare")]
    + [("location", "remote")]
    + [("salary", slug) for slug in cd.salary_slugs()]
    + [("freshness", slug) for slug in cd.freshness_slugs()]
)


@pytest.mark.parametrize("kind,slug", SQLITE_DIMENSIONS)
def test_sql_selects_the_same_jobs(db, add, kind, slug):
    ids = {add(job): job for job in JOBS}
    dimension = cd.get(kind, slug)
    stmt = select(m.JobPostingUrl.id).join(m.JobPosting, m.JobPosting.url_id == m.JobPostingUrl.id).where(dimension.clause(NOW))
    selected = {str(i) for i in db.scalars(stmt)}
    expected = {i for i, job in ids.items() if dimension.matches(job, NOW)}
    assert selected == expected
    assert expected or kind == "category"  # every non-category case picks something here


def _postgres(clause) -> tuple[str, dict]:
    compiled = clause.compile(dialect=postgresql.dialect())
    return " ".join(str(compiled).split()), compiled.params


def test_role_and_area_sql_use_the_jsonb_indexes():
    sql, params = _postgres(cd.get("role", "software-engineer").clause(NOW))
    assert sql.startswith("job_postings.standard_titles @>")  # what ix_job_postings_standard_titles serves
    assert list(params.values()) == [["Software Engineer"]]
    sql, params = _postgres(cd.get("location", geo.metro_by_code(SLC).slug).clause(NOW))
    assert sql.startswith("job_postings.metros @>")  # what ix_job_postings_metros serves
    assert list(params.values()) == [[SLC]]


def test_clause_all_combines_dimensions():
    sql, params = _postgres(cd.clause_all([cd.get("role", "software-engineer"), cd.get("freshness", "posted-today")], NOW))
    assert "standard_titles @>" in sql and "job_postings.posted_at BETWEEN" in sql and " AND " in sql
    assert [TODAY, TODAY] == [v for v in params.values() if isinstance(v, date)]
