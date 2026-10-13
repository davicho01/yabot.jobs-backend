"""Standard titles on Tech postings (app.services.job_standard_titles) and
title search matching them (app.services.jobs.build_job_search_statement).
The model itself is faked here; tests/services/test_standard_titles.py runs
the real one."""

from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects import postgresql

from app.models.enums import JobSector, ScanStatus
from app.models.job_posting import JobPosting
from app.services import job_standard_titles, jobs, standard_titles
from app.services.job_sector import input_hash
from app.services.job_scanner import ScanResult


@pytest.fixture
def model(monkeypatch):
    """A fake model: records what it tags, answers ["Backend Engineer"]."""
    calls = []

    def tag_posting(title, description):
        calls.append(title)
        return ["Backend Engineer"]

    monkeypatch.setattr(standard_titles, "tag_posting", tag_posting)
    monkeypatch.setattr(standard_titles, "model_version", lambda: "test-v1")
    return calls


def _posting(sector=JobSector.ENGINEERING_TECH, **kw) -> JobPosting:
    return JobPosting(title="Senior Go Developer", description="Build APIs.", sector=sector, **kw)


def test_tech_posting_gets_titles_model_and_hash(model):
    posting = _posting()
    job_standard_titles.apply_standard_titles(posting)

    assert posting.standard_titles == ["Backend Engineer"]
    assert posting.standard_title_model == "test-v1"
    assert posting.standard_title_input_hash == input_hash("Senior Go Developer", "Build APIs.")


def test_same_text_keeps_its_titles_without_the_model(model):
    posting = _posting(
        standard_titles=["Software Engineer"],
        standard_title_model="old",
        standard_title_input_hash=input_hash("Senior Go Developer", "Build APIs."),
    )
    job_standard_titles.apply_standard_titles(posting)

    assert posting.standard_titles == ["Software Engineer"]
    assert model == []


def test_changed_text_is_tagged_again(model):
    posting = _posting(standard_titles=["Software Engineer"], standard_title_model="old", standard_title_input_hash="x")
    job_standard_titles.apply_standard_titles(posting)

    assert posting.standard_titles == ["Backend Engineer"]


def test_a_posting_that_isnt_tech_has_no_titles(model):
    posting = _posting(sector=JobSector.SALES, standard_titles=["Sales Engineer"], standard_title_model="old")
    job_standard_titles.apply_standard_titles(posting)

    assert (posting.standard_titles, posting.standard_title_model, posting.standard_title_input_hash) == (None, None, None)
    assert model == []


def test_no_model_leaves_a_tech_posting_pending():
    # tests/conftest.py: no model loaded
    posting = _posting()
    job_standard_titles.apply_standard_titles(posting)

    assert posting.standard_titles is None and posting.standard_title_model is None


def test_upsert_tags_a_tech_posting(scan_db, make_source, make_url, model, monkeypatch):
    monkeypatch.setattr(jobs, "apply_sector", lambda p, title, description: setattr(p, "sector", JobSector.ENGINEERING_TECH))
    url_row = make_url(make_source())
    result = ScanResult(success=True, title="Senior Go Developer", description="Build APIs.")

    posting = jobs._upsert_posting(scan_db, url_row, result, datetime.now(timezone.utc))
    scan_db.commit()

    assert posting.standard_titles == ["Backend Engineer"]


def test_sweep_tags_pending_tech_postings_only(scan_db, make_source, make_url, model):
    source = make_source()
    pending = JobPosting(url_id=make_url(source).id, title="Go Developer", sector=JobSector.ENGINEERING_TECH)
    done = JobPosting(
        url_id=make_url(source).id, title="Done", sector=JobSector.ENGINEERING_TECH, standard_title_model="v0"
    )
    sales = JobPosting(url_id=make_url(source).id, title="Account Executive", sector=JobSector.SALES)
    scan_db.add_all([pending, done, sales])
    scan_db.commit()

    assert job_standard_titles.tag_pending_standard_titles(scan_db) == (1, 0)
    assert model == ["Go Developer"]
    assert pending.standard_titles == ["Backend Engineer"]
    assert sales.standard_titles is None


def test_sweep_stops_when_the_model_is_unavailable(scan_db, make_source, make_url):
    scan_db.add(JobPosting(url_id=make_url(make_source()).id, title="Go Developer", sector=JobSector.ENGINEERING_TECH))
    scan_db.commit()

    assert job_standard_titles.tag_pending_standard_titles(scan_db) == (0, 1)


# ------------------------------------------------------------------ search


def _search_sql(q: str) -> tuple[str, list]:
    stmt, _order, _area = jobs.build_job_search_statement(q=q)
    compiled = stmt.compile(dialect=postgresql.dialect())
    return " ".join(str(compiled).split()), list(compiled.params.values())


def test_a_tech_search_also_matches_its_titles_and_their_related_ones(monkeypatch):
    monkeypatch.setattr(standard_titles, "tag_search", lambda text: ["Data Engineer"])
    sql, params = _search_sql("data eng -senior")

    assert "job_postings.title ILIKE" in sql and "%data eng%" in params
    # the operator ix_job_postings_standard_titles (GIN, jsonb_path_ops) serves, once per title
    assert sql.count("job_postings.standard_titles @>") == 3
    assert [["Data Engineer"], ["Analytics Engineer"], ["Data Architect"]] == [p for p in params if isinstance(p, list)]
    assert "job_postings.title NOT ILIKE" in sql and "%senior%" in params  # exclusions still apply


def test_a_search_that_isnt_a_tech_title_is_a_plain_title_match(monkeypatch):
    monkeypatch.setattr(standard_titles, "tag_search", lambda text: [])
    assert "standard_titles" not in _search_sql("barista")[0]


def test_without_the_model_search_is_a_plain_title_match(scan_db, make_source, make_url):
    # tests/conftest.py: no model loaded. Runs on SQLite, like before titles existed.
    url_row = make_url(make_source(), status=ScanStatus.SUCCESS)
    scan_db.add(JobPosting(url_id=url_row.id, title="Barista", extraction_status=ScanStatus.SUCCESS))
    scan_db.commit()
    stmt, _order, _area = jobs.build_job_search_statement(q="barista")

    assert [row.id for row in scan_db.scalars(stmt)] == [url_row.id]
