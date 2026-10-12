from app.models.enums import EmploymentType, WorkplaceType
from app.services.adapters import base, jazzhr

URL = "https://leapbrands.applytojob.com/apply/sNnLQ7d2Np"
PAGE = """
<meta name="description" content="Apply to Brand President - FEP at Leap Brands in Remote.">
<script type="application/ld+json">{"@type":"Organization","name":"Leap Brands","url":"https://leapbrands.com"}</script>
<div class='job-header'><div class='container'>
  <h2>Brand President - FEP</h2>
  <div class="job-attributes-container">
    <div title="Location"><i class='fa fa-map-marker'></i>Remote</div>
    <div id='resumator-job-employment' title="Type"><i class='fa fa-clock-o'></i>Full Time</div>
  </div>
</div></div>
<div class='page-body job-details'><div class='container'><div class='row'>
  <div class='col col-xs-7 description' id="job-description">
    <h3>Overview</h3><p>The Brand President is responsible for the overall performance of a restaurant brand.</p>
    <div><p>Full P&amp;L accountability.</p></div>
  </div>
  <div class='col col-xs-5'>Apply form</div>
</div></div></div>
"""


def _scan(monkeypatch, html, url=URL):
    monkeypatch.setattr(base, "fetch_html", lambda u: base.FetchedPage(text=html, url=u))
    return jazzhr.scan_job_url(url)


def test_job_is_read_from_the_page_body(monkeypatch):
    result = _scan(monkeypatch, PAGE)

    assert result.success
    assert (result.title, result.company_name, result.location) == ("Brand President - FEP", "Leap Brands", "Remote")
    assert (result.workplace_type, result.employment_type) == (WorkplaceType.REMOTE, EmploymentType.FULL_TIME)
    assert "overall performance" in result.description and "P&L accountability" in result.description
    assert "Apply form" not in result.description  # only the description column
    assert "Apply to Brand President" not in result.description  # not the meta teaser


def test_board_listing_and_other_hosts_are_not_claimed():
    assert jazzhr.scan_job_url("https://leapbrands.applytojob.com/apply/jobs") is None
    assert jazzhr.scan_job_url("https://boards.greenhouse.io/acme/jobs/1") is None
