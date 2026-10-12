from app.services.adapters import base, peopleadmin

URL = "https://winthrop.peopleadmin.com/postings/22085"
LONG = "Act as the resource person in a residence hall office and the first point of contact."
PAGE = f"""
<title>Winthrop University Applicant Portal | Night Host</title>
<h2>Night Host FWS 26/27</h2><h2>Posting Details</h2>
<table>
  <tr><th>Job Purpose</th><td>{LONG}</td></tr>
  <tr><th>Duties and Responsibilities</th><td><ul><li>{LONG}</li><li>Work with Residence Life staff on safety rounds.</li></ul></td></tr>
  <tr><th>Position Location (City)</th><td>Rock Hill</td></tr>
  <tr><th>Open Date</th><td>07/07/2026</td></tr>
  <tr><th>EEO Statement</th><td>Winthrop University is an equal opportunity employer and values diversity in all forms.</td></tr>
  <tr><th>Type</th><td>Student</td></tr>
</table>
"""


def _scan(monkeypatch, html, url=URL):
    monkeypatch.setattr(base, "fetch_html", lambda u: base.FetchedPage(text=html, url=u))
    return peopleadmin.scan_job_url(url)


def test_posting_table_becomes_the_description(monkeypatch):
    result = _scan(monkeypatch, PAGE)

    assert result.success and result.title == "Night Host FWS 26/27"
    assert "Job Purpose" in result.description and "Duties and Responsibilities" in result.description
    assert "safety rounds" in result.description
    assert "equal opportunity" not in result.description  # boilerplate skipped
    assert "Student" not in result.description  # short fields aren't description
    assert (result.location, str(result.posted_at)) == ("Rock Hill", "2026-07-07")


def test_search_pages_and_other_hosts_are_not_claimed():
    assert peopleadmin.scan_job_url("https://winthrop.peopleadmin.com/postings/search") is None
    assert peopleadmin.scan_job_url("https://boards.greenhouse.io/acme/jobs/1") is None
