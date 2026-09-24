import httpx
import pytest

from app.services.adapters import base
from app.services.browser_fetch import RenderedPage
from tests.conftest import FakeResponse

URL = "https://delta.avature.net/en_US/careers/JobDetail/some-job/123"


def test_fetch_html_falls_back_to_browser_render_on_empty_body(monkeypatch):
    # Some WAFs (verified live: delta.avature.net) answer a plain httpx
    # request with a 2xx status and an empty body instead of an error status
    # - raise_for_status() never catches this, so without an explicit empty-
    # body check fetch_html would return the challenge page as if it were
    # real content.
    monkeypatch.setattr(base, "_fetch_direct", lambda _url: FakeResponse(text="", status_code=202, url=URL))
    monkeypatch.setattr(
        base, "fetch_rendered_page", lambda _url, **_kw: RenderedPage(html="<html>real content</html>", url=URL)
    )

    page = base.fetch_html(URL)

    assert page.text == "<html>real content</html>"


def test_fetch_html_raises_when_empty_body_and_render_fallback_fails(monkeypatch):
    monkeypatch.setattr(base, "_fetch_direct", lambda _url: FakeResponse(text="   ", status_code=200, url=URL))
    monkeypatch.setattr(base, "fetch_rendered_page", lambda _url, **_kw: None)

    with pytest.raises(httpx.HTTPError):
        base.fetch_html(URL)


def test_fetch_html_uses_direct_response_when_body_present(monkeypatch):
    monkeypatch.setattr(base, "_fetch_direct", lambda _url: FakeResponse(text="<html>ok</html>", url=URL))
    monkeypatch.setattr(
        base, "fetch_rendered_page", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("should not be called"))
    )

    page = base.fetch_html(URL)

    assert page.text == "<html>ok</html>"


# Shaped after a real jobs.smartrecruiters.com posting, which publishes full
# schema.org JobPosting data as page microdata (itemscope/itemtype/itemprop
# attributes) rather than an application/ld+json block.
_MICRODATA_HTML = """
<main itemscope itemtype="http://schema.org/JobPosting">
  <h1 itemprop="title">Senior Frontend Software Engineer</h1>
  <li itemprop="jobLocation" itemscope itemtype="http://schema.org/Place">
    <span itemprop="address" itemscope itemtype="http://schema.org/PostalAddress">
      <meta itemprop="addressLocality" content="Sydney">
      <meta itemprop="addressRegion" content="New South Wales">
      <meta itemprop="addressCountry" content="Australia">
    </span>
  </li>
  <li itemprop="employmentType">Full-time</li>
  <div itemprop="hiringOrganization" itemscope itemtype="http://schema.org/Organization">
    <meta itemprop="name" content="Canva">
  </div>
  <meta itemprop="datePosted" content="2026-09-18T04:16:43.529Z">
  <div itemprop="description"><p>Join the team. We're hiring!</p></div>
</main>
"""


def test_extract_microdata_posting_parses_json_ld_shaped_fields():
    result = base.extract_microdata_posting(_MICRODATA_HTML)

    assert result["title"] == "Senior Frontend Software Engineer"
    assert result["employmentType"] == "Full-time"
    assert result["datePosted"] == "2026-09-18T04:16:43.529Z"
    assert result["hiringOrganization"] == {"name": "Canva"}
    assert result["jobLocation"] == {
        "address": {
            "addressLocality": "Sydney",
            "addressRegion": "New South Wales",
            "addressCountry": "Australia",
        }
    }
    assert "Join the team" in result["description"]

    # Confirms the result plugs straight into the existing job_ld_* helpers
    # unchanged, same as a JSON-LD-sourced dict would.
    assert base.job_ld_location(result) == "Sydney, New South Wales, Australia"
    assert base.job_ld_workplace_type(result) == "onsite"
    assert base.job_ld_employment_type(result) == "full_time"
    assert base.job_ld_posted_at(result).isoformat() == "2026-09-18"


def test_extract_microdata_posting_none_when_no_jobposting_itemscope():
    assert base.extract_microdata_posting("<html><body>No structured data here</body></html>") is None


def test_extract_microdata_posting_omits_missing_fields():
    html = """
    <div itemscope itemtype="https://schema.org/JobPosting">
      <h1 itemprop="title">Staff Engineer</h1>
    </div>
    """
    result = base.extract_microdata_posting(html)

    assert result == {"title": "Staff Engineer"}


def test_extract_microdata_posting_reads_valid_through():
    html = """
    <main itemscope itemtype="http://schema.org/JobPosting">
      <h1 itemprop="title">Staff Software Engineer</h1>
      <meta itemprop="validThrough" content="2026-09-21T23:27:15.829Z">
    </main>
    """
    result = base.extract_microdata_posting(html)

    assert result["validThrough"] == "2026-09-21T23:27:15.829Z"


def test_job_ld_is_expired_true_for_past_valid_through():
    # A real SmartRecruiters posting past its validThrough date: the
    # title/location microdata is still intact, but the real job content
    # is replaced with "This job has expired" — the scanner needs this
    # signal to avoid reporting that as an ordinary successful scan.
    assert base.job_ld_is_expired({"validThrough": "2026-09-21T23:27:15.829Z"}) is True


def test_job_ld_is_expired_false_for_future_valid_through():
    assert base.job_ld_is_expired({"validThrough": "2099-01-01T00:00:00.000Z"}) is False


def test_job_ld_is_expired_false_when_missing():
    assert base.job_ld_is_expired({}) is False


def test_job_ld_is_expired_false_when_unparseable():
    assert base.job_ld_is_expired({"validThrough": "not a date"}) is False
