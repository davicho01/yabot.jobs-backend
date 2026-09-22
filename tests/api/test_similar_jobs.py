import itertools
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes.jobs import similar_jobs
from app.db.base import Base
from app.models.enums import ScanStatus
from app.services.job_dedup import normalize_company_name, normalize_title

_url_counter = itertools.count()


@pytest.fixture
def db() -> Session:
    # Local db fixture (not tests/api/conftest.py's, which only has
    # SavedSearch) — similar_jobs needs JobPostingUrl/JobPosting.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.JobPostingUrl.__table__, m.JobPosting.__table__])
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


def _make_posting(
    db,
    *,
    title: str | None = "Backend Engineer",
    company_name: str = "Acme",
    extraction_status: str = ScanStatus.SUCCESS,
) -> m.JobPostingUrl:
    n = next(_url_counter)
    url_row = m.JobPostingUrl(
        url=f"https://example.com/jobs/similar-{n}",
        normalized_url=f"https://example.com/jobs/similar-{n}",
        url_hash=f"similar-hash-{n}",
        domain="example.com",
    )
    db.add(url_row)
    db.flush()
    db.add(
        m.JobPosting(
            url_id=url_row.id,
            company_name=company_name,
            title=title,
            company_key=normalize_company_name(company_name),
            title_key=normalize_title(title),
            extraction_status=extraction_status,
        )
    )
    db.commit()
    return url_row


def test_similar_jobs_returns_404_for_an_unknown_url_id(db):
    with pytest.raises(HTTPException) as exc_info:
        similar_jobs(uuid.uuid4(), db=db)
    assert exc_info.value.status_code == 404


def test_similar_jobs_is_empty_for_a_posting_that_has_not_scanned_yet(db):
    url_row = _make_posting(db, title=None, extraction_status=ScanStatus.PENDING)

    result = similar_jobs(url_row.id, db=db)

    assert result.same_company == []
    assert result.similar_title == []


def test_similar_jobs_splits_same_company_and_similar_title(db):
    url_row = _make_posting(db, title="Backend Engineer", company_name="Acme")
    _make_posting(db, title="Data Analyst", company_name="Acme")  # same_company
    _make_posting(db, title="Backend Engineer", company_name="Globex")  # similar_title

    result = similar_jobs(url_row.id, db=db)

    assert [job.posting.company_name for job in result.same_company] == ["Acme"]
    assert [job.posting.company_name for job in result.similar_title] == ["Globex"]
