"""Tests for app.services.collection_stats — a collection's numbers, counted
over the generator's live index (LinkJob) and straight from the database
(the two must agree). Same in-memory SQLite setup as
test_collection_dimensions.py."""

import itertools
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import app.models as m
from app.db.base import Base
from app.models.enums import JobSector, ScanStatus, WorkplaceType
from app.services import collection_dimensions as cd
from app.services import static_job_pages as sjp
from app.services.collection_stats import CollectionStats, calculate_collection_stats, collection_stats_from_db


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(_type, _compiler, **_kw):
    return "JSON"


NOW = datetime(2026, 10, 26, 18, 0, tzinfo=timezone.utc)  # 2 pm Eastern
TODAY = date(2026, 10, 26)
counter = itertools.count()


def job(**kw) -> sjp.LinkJob:
    n = next(counter)
    base = dict(
        url_id=f"job-{n}", title=f"Job {n}", company_name=None, domain=f"company{n}.com", company_key=None,
        title_key=None, sector=JobSector.ENGINEERING_TECH, metros=(), location=None,
        workplace_type=WorkplaceType.ONSITE, posted_at=TODAY - timedelta(days=10),
        found_at=NOW - timedelta(days=10), scanned="2026-10-16T00:00:00+00:00",
    )
    base.update(kw)
    return sjp.LinkJob(**base)


def stats(jobs, *dimensions) -> CollectionStats:
    return calculate_collection_stats(jobs, [cd.get(kind, slug) for kind, slug in dimensions], NOW)


# ------------------------------------------------------------- counting


def test_counts_only_the_collections_open_jobs():
    jobs = [
        job(company_key="acme", workplace_type=WorkplaceType.REMOTE),
        job(company_key="acme", workplace_type=WorkplaceType.HYBRID),
        job(company_key="globex"),
        job(),  # no company key: counted by its domain
        job(workplace_type=WorkplaceType.UNKNOWN),
        job(company_key="initech", closed="2026-10-25T00:00:00+00:00"),  # closed: not counted
        job(company_key="umbrella", sector=JobSector.HEALTHCARE),  # another collection
    ]
    s = stats(jobs, ("category", "engineering-tech"))
    assert (s.open_jobs, s.companies_hiring) == (5, 4)
    assert (s.remote_jobs, s.hybrid_jobs, s.onsite_jobs) == (1, 1, 2)  # unknown is none of them


def test_empty_collection():
    s = stats([job(sector=JobSector.HEALTHCARE)], ("category", "engineering-tech"))
    assert s == CollectionStats(0, 0, 0, 0, 0, 0, 0, 0, 0, None, None)


def test_role_collections_count_from_the_index():
    jobs = [job(standard_titles=("Software Engineer",)), job(standard_titles=("Software Developer",)), job()]
    assert stats(jobs, ("role", "software-engineer")).open_jobs == 1


# --------------------------------------------------------------- salary


def test_salary_counts_and_medians():
    jobs = [
        job(salary_min=80_000, salary_max=100_000),
        job(salary_min=100_000, salary_max=140_000, salary_currency="USD"),
        job(salary_min=120_000),  # a min only
        job(salary_max=200_000),  # a max only
        job(salary_min=40, salary_max=55),  # hourly: listed, but not in the medians
        job(salary_min=90_000, salary_currency="EUR"),  # not USD: not counted
        job(salary_min=150_000_000),  # a parsing error: not counted
        job(),
    ]
    s = stats(jobs)
    assert s.jobs_with_salary == 5
    assert s.median_salary_min == 100_000  # 80k, 100k, 120k
    assert s.median_salary_max == 140_000  # 100k, 140k, 200k


def test_even_median_is_rounded_to_a_whole_dollar():
    s = stats([job(salary_min=70_001), job(salary_min=80_000)])
    assert s.median_salary_min == 75_000  # 75,000.5 rounds to even
    assert s.median_salary_max is None


def test_only_hourly_figures_give_no_median():
    s = stats([job(salary_min=20, salary_max=30)])
    assert (s.jobs_with_salary, s.median_salary_min, s.median_salary_max) == (1, None, None)


# ---------------------------------------------- the 24-hour windows (§55)


def test_found_today_but_posted_a_month_ago_is_found_not_posted():
    s = stats([job(posted_at=TODAY - timedelta(days=30), found_at=NOW - timedelta(hours=2))])
    assert (s.jobs_found_24h, s.jobs_posted_24h) == (1, 0)


def test_found_and_verified_are_the_last_24_hours_exactly():
    jobs = [
        job(found_at=NOW - timedelta(hours=23, minutes=59)),
        job(found_at=NOW - timedelta(hours=24)),  # the window's start is out
        job(found_at=NOW + timedelta(minutes=5)),  # the future is bad data
        job(last_seen_at=NOW - timedelta(hours=1)),
        job(last_seen_at=NOW - timedelta(hours=25)),
        job(last_seen_at=None, found_at=NOW - timedelta(hours=1)),  # never crawled: found, not verified
    ]
    s = stats(jobs)
    assert (s.jobs_found_24h, s.jobs_verified_24h) == (2, 1)


def test_posted_counts_the_eastern_days_the_window_touches():
    # 2 pm Eastern on the 26th: the window reaches back to 2 pm on the 25th
    jobs = [
        job(posted_at=TODAY),
        job(posted_at=TODAY - timedelta(days=1)),
        job(posted_at=TODAY - timedelta(days=2)),
        job(posted_at=TODAY + timedelta(days=1)),  # future: bad data
        job(posted_at=None, found_at=NOW - timedelta(hours=1)),  # no employer date: never posted
    ]
    assert stats(jobs).jobs_posted_24h == 2
    # 11 pm Eastern on the 26th (03:00 UTC on the 27th): still the 25th's last hour
    late = datetime(2026, 10, 27, 3, 0, tzinfo=timezone.utc)
    assert calculate_collection_stats(jobs, [], late).jobs_posted_24h == 2
    # 1 am Eastern on the 27th: the 25th has left the window, and the 27th is today
    assert calculate_collection_stats(jobs, [], late + timedelta(hours=2)).jobs_posted_24h == 2


def test_naive_sqlite_timestamps_are_utc():
    s = stats([job(found_at=(NOW - timedelta(hours=1)).replace(tzinfo=None))])
    assert s.jobs_found_24h == 1


# ------------------------------------------- the database agrees


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.JobPostingUrl.__table__, m.JobPosting.__table__])
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


def _store(db, jobs: list[sjp.LinkJob]) -> None:
    for n, j in enumerate(jobs):
        url = m.JobPostingUrl(
            url=f"https://{j.domain}/jobs/{n}", normalized_url=f"https://{j.domain}/jobs/{n}", url_hash=f"hash-{n}",
            domain=j.domain, created_at=j.found_at, last_seen_at=j.last_seen_at,
            closed_at=datetime.fromisoformat(j.closed) if j.closed else None,
        )
        db.add(url)
        db.flush()
        db.add(
            m.JobPosting(
                url_id=url.id, title=j.title, company_key=j.company_key, sector=j.sector,
                standard_titles=list(j.standard_titles) or None, metros=list(j.metros), workplace_type=j.workplace_type,
                salary_min=j.salary_min, salary_max=j.salary_max, salary_currency=j.salary_currency,
                posted_at=j.posted_at, country="US", extraction_status=ScanStatus.SUCCESS,
            )
        )
    db.commit()


JOBS = [
    job(company_key="acme", workplace_type=WorkplaceType.REMOTE, salary_min=80_000, salary_max=100_000),
    job(company_key="acme", workplace_type=WorkplaceType.HYBRID, salary_min=100_000, salary_max=140_000),
    job(company_key="globex", salary_min=120_000, salary_currency="USD"),
    job(salary_max=200_000, workplace_type=WorkplaceType.REMOTE),
    job(salary_min=40, salary_max=55),
    job(salary_min=90_000, salary_currency="EUR"),
    job(salary_min=150_000_000),
    job(sector=JobSector.HEALTHCARE, company_key="umbrella", workplace_type=WorkplaceType.REMOTE, salary_min=60_000),
    job(company_key="initech", closed="2026-10-25T00:00:00+00:00"),
    job(posted_at=TODAY - timedelta(days=30), found_at=NOW - timedelta(hours=2)),
    job(posted_at=TODAY, found_at=NOW - timedelta(hours=23)),
    job(posted_at=TODAY - timedelta(days=1), found_at=NOW - timedelta(hours=25)),
    job(posted_at=None, found_at=NOW - timedelta(hours=1)),
    job(last_seen_at=NOW - timedelta(hours=1), crawled=True),
    job(last_seen_at=NOW - timedelta(hours=30)),
]


@pytest.mark.parametrize(
    "dimensions",
    [
        (),
        (("category", "engineering-tech"),),
        (("category", "healthcare"),),
        (("location", "remote"),),
        (("category", "engineering-tech"), ("location", "remote")),
        (("category", "engineering-tech"), ("salary", "100k-plus")),
        (("category", "engineering-tech"), ("freshness", "found-today")),
    ],
)
def test_the_database_gives_the_same_numbers(db, dimensions):
    _store(db, JOBS)
    index = sjp.load_link_index(db, NOW)
    assert len(index) == len(JOBS)
    dims = [cd.get(kind, slug) for kind, slug in dimensions]
    from_index = calculate_collection_stats(index.values(), dims, NOW)
    assert collection_stats_from_db(db, dims, NOW) == from_index
    if not dimensions:
        assert from_index == calculate_collection_stats(JOBS, [], NOW)
        assert from_index.open_jobs == len(JOBS) - 1
        assert all(v for v in from_index.as_dict().values())  # every number is exercised


def test_the_index_carries_the_collection_fields(db):
    _store(db, [job(standard_titles=("Software Engineer",), salary_min=1, salary_max=2, salary_currency="USD",
                    last_seen_at=NOW - timedelta(hours=1))])
    [loaded] = sjp.load_link_index(db, NOW).values()
    assert loaded.standard_titles == ("Software Engineer",)
    assert (loaded.salary_min, loaded.salary_max, loaded.salary_currency) == (1, 2, "USD")
    assert loaded.last_seen_at.replace(tzinfo=timezone.utc) == NOW - timedelta(hours=1)
