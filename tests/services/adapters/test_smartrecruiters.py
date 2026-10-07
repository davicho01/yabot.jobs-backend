import httpx
import pytest

from app.services.adapters import base, smartrecruiters
from app.services.ats_adapters import board_url_for_key, detect_ats_source

JOB_URL = "https://jobs.smartrecruiters.com/WesternDigital/744000153868469-staff-developer-software-development"


def _job_page(*, formatted="Irvine, CA", country="United States", salary="124,000.00-165,300.00"):
    return f"""<html><head><meta property="og:title" content="Staff Developer" /></head><body>
<h1 class="job-title" itemprop="title">Staff Developer, Software Development</h1>
<ul class="job-details spl-list-none"><li itemprop="jobLocation" itemscope><span class="job-detail" itemprop="address">
<spl-job-location formattedAddress="{formatted}" workplaceDescription="" workplaceType="hybrid"></spl-job-location>
<meta itemprop="addressCountry" content="{country}"><meta itemprop="addressLocality" content="Irvine">
<meta itemprop="addressRegion" content="CA"></span></li>
<li class="job-detail" itemprop="employmentType">Full-time</li>
<li class="job-detail">Salary Range: {salary}</li></ul>
<div itemprop="hiringOrganization" itemscope><meta itemprop="name" content="Western Digital"></div>
<meta itemprop="datePosted" content="2026-10-06T22:21:16.569Z">
<div class="job-sections"><div itemprop="description">
<section class="job-section" id="st-jobDescription"><div><h2 class="title">Job Description</h2></div>
<div class="wysiwyg"><p>Build storage software.</p></div></section></div></div>
<div class="footer">Apply</div></body></html>"""


@pytest.mark.parametrize("url,key", [
    ("https://careers.smartrecruiters.com/WesternDigital", "westerndigital"),
    (JOB_URL, "westerndigital"),
    ("https://jobs.smartrecruiters.com/my-applications/Wabtec?dcr_ci=Wabtec", "wabtec"),
    ("https://join.smartrecruiters.com/MattelInc/7ba8af16-mattelconnect", "mattelinc"),
    ("https://jobs.smartrecruiters.com/oneclick-ui/company/x", None),
    ("https://api.smartrecruiters.com/v1/companies/WesternDigital/postings", None),
    ("https://careers.smartrecruiters.com.evil.test/WesternDigital", None),
])
def test_match(url, key):
    assert smartrecruiters._match(url) == key


def test_detected_board_is_the_career_site():
    ats_type, key = detect_ats_source(JOB_URL)
    assert ats_type == "smartrecruiters"
    assert board_url_for_key(ats_type, key, JOB_URL) == "https://careers.smartrecruiters.com/westerndigital"


def test_listing_pages_through_the_career_site_until_a_page_adds_nothing(monkeypatch):
    pages = {
        0: '<a href="https://jobs.smartrecruiters.com/WesternDigital/1000001-a">A</a>'
           '<a href="https://jobs.smartrecruiters.com/WesternDigital/1000002-b">B</a>',
        1: '<a href="https://jobs.smartrecruiters.com/WesternDigital/1000003-c">C</a>',
        2: '<a href="https://jobs.smartrecruiters.com/WesternDigital/1000003-c">C</a>',  # nothing new: stop
    }
    calls = []

    def fake_get(url, params=None, timeout=None, **_):
        calls.append(params["page"])
        assert url == "https://careers.smartrecruiters.com/westerndigital/api/more"
        return httpx.Response(200, text=pages[params["page"]], request=httpx.Request("GET", url))

    monkeypatch.setattr(smartrecruiters, "get_with_retry", fake_get)

    urls = smartrecruiters._fetch_jobs("westerndigital")

    assert [u.rsplit("/", 1)[1] for u in urls] == ["1000001-a", "1000002-b", "1000003-c"]
    assert calls == [0, 1, 2]


def test_an_unknown_company_fails_loudly(monkeypatch):
    def fake_get(url, **_):
        return httpx.Response(404, text="", request=httpx.Request("GET", url))

    monkeypatch.setattr(smartrecruiters, "get_with_retry", fake_get)
    with pytest.raises(httpx.HTTPStatusError):
        smartrecruiters._fetch_jobs("nosuchcompany")


def _serve(monkeypatch, status, text):
    monkeypatch.setattr(
        smartrecruiters, "get_with_retry",
        lambda url, **_: httpx.Response(status, text=text, request=httpx.Request("GET", url)),
    )


def test_scan_reads_the_job_page_microdata(monkeypatch):
    _serve(monkeypatch, 200, _job_page())

    result = smartrecruiters.scan_job_url(JOB_URL)

    assert result.success
    assert result.title == "Staff Developer, Software Development"
    assert result.company_name == "Western Digital"
    assert result.location == "Irvine, CA"
    assert (result.workplace_type, result.employment_type) == ("hybrid", "full_time")
    assert str(result.posted_at) == "2026-10-06"
    assert "Build storage software." in result.description and "Apply" not in result.description
    assert (result.salary_min, result.salary_max, result.salary_currency) == (124000, 165300, "USD")


def test_a_street_address_becomes_the_place_and_a_non_us_range_isnt_guessed(monkeypatch):
    _serve(monkeypatch, 200, _job_page(formatted="30 Main St, Irvine, CA", country="Canada"))

    result = smartrecruiters.scan_job_url(JOB_URL)

    assert result.location == "Irvine, CA, Canada"
    assert result.salary_min is None and result.salary_currency is None


def test_a_removed_posting_is_expired(monkeypatch):
    _serve(monkeypatch, 400, "<title>SmartRecruiters</title>")
    monkeypatch.setattr(base, "fetch_html", lambda url: pytest.fail("must not fall back to a browser render"))

    result = smartrecruiters.scan_job_url(JOB_URL)

    assert not result.success and result.expired


def test_other_urls_are_not_claimed():
    assert smartrecruiters.scan_job_url("https://careers.smartrecruiters.com/WesternDigital") is None
    assert smartrecruiters.scan_job_url("https://boards.greenhouse.io/acme/jobs/1") is None
