from app.models.enums import EmploymentType
from app.services.adapters import base, suu
from tests.conftest import FakeResponse

_ROW = '<li><span class="text-body-secondary p-0" style="x">{0}:</span>\n<span style="y">{1}</span></li>'
_JOB_HTML = (
    '<h1 class="d-flex"><span class="">Tutoring Center Tutor</span></h1><ul class="job-detail">'
    + _ROW.format("Category", "On-Campus")
    + _ROW.format("Position Type", "Student Hourly")
    + _ROW.format("Posted on", "August 11, 2026")
    + _ROW.format("Wage", "$13-15/hr depending on qualifications")
    + _ROW.format("Location", "Cedar City, UT")
    + '</ul><div class="body"><h4>Description:</h4><p>Tutors assist students.</p></div>'
)


def test_fetch_jobs_reads_listing_hrefs_with_trailing_space(monkeypatch):
    html = '<a href="/jobs/55648 ">a</a><a href="/jobs/55648 ">b</a><a href="/jobs">home</a>'
    monkeypatch.setattr(suu, "get_with_retry", lambda url, timeout: FakeResponse(text=html))
    assert suu._fetch_jobs("suu") == ["https://my.suu.edu/jobs/55648"]


def test_scan_reads_title_and_detail_fields(monkeypatch):
    monkeypatch.setattr(base, "fetch_html", lambda u: base.FetchedPage(text=_JOB_HTML, url=u))
    result = suu.scan_job_url("https://my.suu.edu/jobs/55648")
    assert result.title == "Tutoring Center Tutor"
    assert result.location == "Cedar City, UT"
    assert result.employment_type == EmploymentType.PART_TIME
    assert result.posted_at.isoformat() == "2026-08-11"
    assert "Tutors assist students." in result.description


def test_scan_flags_closed_posting(monkeypatch):
    html = _JOB_HTML + "This job posting is no longer active."
    monkeypatch.setattr(base, "fetch_html", lambda u: base.FetchedPage(text=html, url=u))
    result = suu.scan_job_url("https://my.suu.edu/jobs/55988")
    assert not result.success and result.expired


def test_scan_ignores_listing_url():
    assert suu.scan_job_url("https://my.suu.edu/jobs") is None
