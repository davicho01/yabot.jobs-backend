import itertools

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes import jobs as jobs_routes
from app.api.routes.jobs import list_job_urls
from app.db.base import Base
from app.models.enums import ScanStatus

_url_counter = itertools.count()


@pytest.fixture
def db() -> Session:
    # Local db fixture (like test_similar_jobs.py's) — list_job_urls needs
    # JobPostingUrl/JobPosting.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.JobPostingUrl.__table__, m.JobPosting.__table__])
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


def _make_postings(db, count: int) -> None:
    for _ in range(count):
        n = next(_url_counter)
        url_row = m.JobPostingUrl(
            url=f"https://example.com/jobs/list-{n}",
            normalized_url=f"https://example.com/jobs/list-{n}",
            url_hash=f"list-hash-{n}",
            domain="example.com",
        )
        db.add(url_row)
        db.flush()
        db.add(m.JobPosting(url_id=url_row.id, title="Engineer", extraction_status=ScanStatus.SUCCESS))
    db.commit()


def _list(db, **kwargs):
    params = dict(
        q=None, location=None, metro=None, radius=25, company=None, posted_within_days=None,
        workplace_type=None, sector=None, salary_min=None, salary_max=None, page=1, page_size=2,
        current_user=None, db=db,
    )
    return list_job_urls(**{**params, **kwargs})


def test_total_is_exact_when_matches_fit_within_the_count_window(db, monkeypatch):
    monkeypatch.setattr(jobs_routes, "COUNT_AHEAD", 5)
    _make_postings(db, 5)

    result = _list(db)

    assert result.total == 5
    assert result.total_is_capped is False
    assert len(result.items) == 2


def test_total_is_capped_past_the_count_window_and_grows_with_the_page(db, monkeypatch):
    monkeypatch.setattr(jobs_routes, "COUNT_AHEAD", 4)
    _make_postings(db, 10)

    first = _list(db, page=1)
    assert (first.total, first.total_is_capped) == (4, True)

    # Page 3 starts at row 4, so the window reaches row 8.
    third = _list(db, page=3)
    assert (third.total, third.total_is_capped) == (8, True)

    # Page 4's window (6 + 4) covers all 10.
    fourth = _list(db, page=4)
    assert (fourth.total, fourth.total_is_capped) == (10, False)
