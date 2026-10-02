from app.models.enums import EmploymentType
from app.services.adapters import base, canyons
from tests.conftest import FakeResponse

_JOB_HTML = """<div id="example2"><table><tr><td><div class="h1">
<h1><b>CTE-STEM/Robotics Grades 6 - 8 - Draper Park Middle School</b></h1></div></td></tr></table>
<table><tr><td><b>Category:</b></td><td>Licensed</td></tr>
<tr><td><b>Location:</b></td><td>Draper Park Middle School<br></td></tr>
<tr><td><b>Job Type:</b></td><td>Part-Time</td></tr>
<tr><td colspan="2"><b>Job Description:</b><p>Teach robotics.</p></td></tr></table></div>"""


def test_fetch_jobs_parses_datatables_feed_with_raw_control_chars(monkeypatch):
    feed = '{"sEcho": 1, "aaData": [["Pool\t2026", "Special", "<a href=viewjob.cfm?jid=25362>Read More</a>"],\n["X", "Y", "<a href=viewjob.cfm?jid=25373>Read More</a>"]]}'
    seen = {}

    def fake_get(url, params, headers, timeout):
        seen.update(params)
        return FakeResponse(text=feed)

    monkeypatch.setattr(canyons, "get_with_retry", fake_get)
    assert canyons._fetch_jobs("canyons") == [
        "https://jobs.canyonsdistrict.org/hr/viewjob.cfm?jid=25362",
        "https://jobs.canyonsdistrict.org/hr/viewjob.cfm?jid=25373",
    ]
    assert seen["cat"] == seen["loc"] == seen["jt"] == 0


def test_scan_reads_title_type_and_description(monkeypatch):
    monkeypatch.setattr(base, "fetch_html", lambda u: base.FetchedPage(text=_JOB_HTML, url=u))
    result = canyons.scan_job_url("https://jobs.canyonsdistrict.org/hr/viewjob.cfm?jid=25373")
    assert result.title == "CTE-STEM/Robotics Grades 6 - 8 - Draper Park Middle School"
    assert result.employment_type == EmploymentType.PART_TIME
    assert result.location == "Sandy, UT"
    assert "Teach robotics." in result.description


def test_scan_flags_login_wall_as_expired(monkeypatch):
    html = "<title>HR - Job Postings</title><h3>Login Required</h3>"
    monkeypatch.setattr(base, "fetch_html", lambda u: base.FetchedPage(text=html, url=u))
    result = canyons.scan_job_url("https://jobs.canyonsdistrict.org/hr/viewjob.cfm?jid=1")
    assert not result.success and result.expired
