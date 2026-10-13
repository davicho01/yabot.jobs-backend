"""Title search also matching standard titles (app.services.jobs.build_job_search_statement).
The search model is faked here; tests/services/test_standard_titles.py runs the real one."""

from sqlalchemy.dialects import postgresql

from app.models.enums import ScanStatus
from app.models.job_posting import JobPosting
from app.services import jobs, standard_titles


def _search_sql(q: str) -> tuple[str, list]:
    stmt, _order, _area = jobs.build_job_search_statement(q=q)
    compiled = stmt.compile(dialect=postgresql.dialect())
    return " ".join(str(compiled).split()), list(compiled.params.values())


def test_a_tech_search_also_matches_its_titles_and_their_related_ones(monkeypatch):
    monkeypatch.setattr(standard_titles, "tag_search", lambda text: ["Data Engineer"])
    sql, params = _search_sql("data eng -senior")

    assert "job_postings.title ILIKE" in sql and "%data%" in params and "%eng%" in params  # each word
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
