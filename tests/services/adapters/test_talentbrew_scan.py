from app.services.adapters import base, talentbrew

URL = "https://careers.netapp.com/job/wichita/director-product-management/27600/12345"
POSTING = "<p>" + "Lead product strategy for storage. " * 12 + "</p>"
PAGE = f"""
<meta property="og:title" content="Search for Cloud Technology Jobs and Careers with NetApp">
<script type="application/ld+json">{{"@type":"JobPosting","title":"Director, Product Management",
  "datePosted":"2026-10-10","hiringOrganization":{{"@type":"Organization","name":"NetApp"}}, "broken": }}</script>
<h1>Explore roles where you can make an impact</h1>
<span class="job-location">Wichita, Kansas</span>
<div class="ats-description ajd_job-details__ats-description"><span>Requisition number: 2389407</span></div>
<div class="ats-description ajd_job-details__ats-description">{POSTING}</div>
"""


def _scan(monkeypatch, html, url=URL):
    monkeypatch.setattr(base, "fetch_html", lambda u: base.FetchedPage(text=html, url=u))
    return talentbrew.scan_job_url(url)


def test_job_is_read_from_the_page_despite_broken_json_ld(monkeypatch):
    result = _scan(monkeypatch, PAGE)

    assert result.success
    assert result.title == "Director, Product Management"  # not the h1 banner
    assert "Lead product strategy for storage." in result.description
    assert "Requisition number" not in result.description  # the longest block wins
    assert (result.company_name, result.location, str(result.posted_at)) == ("NetApp", "Wichita, Kansas", "2026-10-10")


def test_page_without_the_description_block_goes_to_the_generic_scanner(monkeypatch):
    assert _scan(monkeypatch, "<h1>Some other site</h1>") is None


def test_urls_without_the_job_path_shape_are_not_claimed():
    assert talentbrew.scan_job_url("https://careers.netapp.com/search-jobs") is None
    assert talentbrew.scan_job_url("https://boards.greenhouse.io/acme/jobs/1") is None
