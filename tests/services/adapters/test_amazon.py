from datetime import datetime, timedelta, timezone

from app.services.adapters import amazon
from app.services.adapters.base import RECENT_WINDOW_DAYS
from tests.conftest import FakeResponse


def _dated(job_path: str, days_ago: int) -> dict:
    day = datetime.now(timezone.utc).date() - timedelta(days=days_ago)
    return {"job_path": job_path, "posted_date": day.strftime("%B %d, %Y")}


def test_fetch_jobs_keeps_postings_within_window_and_stops_at_boundary(monkeypatch):
    page = [
        _dated("/a", 0),
        _dated("/b", RECENT_WINDOW_DAYS - 1),  # last day still inside the window
        _dated("/c", RECENT_WINDOW_DAYS),  # first day outside the window
    ]
    calls = []

    def fake_get(url, params, **kwargs):
        calls.append(params["offset"])
        return FakeResponse(json_data={"jobs": page})

    monkeypatch.setattr(amazon, "get_with_retry", fake_get)
    urls = amazon._fetch_jobs("amazon")
    assert urls == [amazon._AMAZON_JOB_BASE_URL + "/a", amazon._AMAZON_JOB_BASE_URL + "/b"]
    assert calls == [0]


def test_fetch_jobs_ignores_postings_with_unparseable_dates(monkeypatch):
    page = [{"job_path": "/a", "posted_date": "not a date"}]
    monkeypatch.setattr(amazon, "get_with_retry", lambda *a, **k: FakeResponse(json_data={"jobs": page}))
    assert amazon._fetch_jobs("amazon") == []


def test_fetch_jobs_stops_after_500_recent_jobs(monkeypatch):
    calls = []

    def fake_get(url, params, **kwargs):
        offset = params["offset"]
        calls.append(offset)
        return FakeResponse(json_data={"jobs": [
            _dated(f"/job/{i}", 0) for i in range(offset, offset + params["result_limit"])
        ]})

    monkeypatch.setattr(amazon, "get_with_retry", fake_get)
    urls = amazon._fetch_jobs("amazon")
    assert len(urls) == 500
    assert len(set(urls)) == 500
    assert calls == [0, 100, 200, 300, 400]


def _job_page(location_line: str) -> str:
    return (
        '<div id="job-detail-body">'
        '<div class="section"><h2>Description</h2><p>Build things.</p></div>'
        '<div class="section"><h2>Basic Qualifications</h2><p>BS degree</p></div>'
        '</div>'
        '<div class="sidebar">'
        '<div class="association location-icon"><ul class="association-content">'
        f'<li>{location_line}</li></ul></div>'
        '</div>'
    )


def test_extract_parses_description_sections_and_onsite_location():
    result = amazon.extract("https://www.amazon.jobs/en/jobs/12345", _job_page("Seattle, WA, USA"))
    assert result.company_name == "Amazon"
    assert result.location == "Seattle, WA, USA"
    assert result.workplace_type == "onsite"
    assert "### Description" in result.description
    assert "Build things." in result.description
    assert "### Basic Qualifications" in result.description
    assert "BS degree" in result.description


def test_extract_virtual_location_is_remote():
    result = amazon.extract("https://www.amazon.jobs/en/jobs/12345", _job_page("Virtual, USA"))
    assert result.workplace_type == "remote"


def test_extract_company_name_set_even_when_html_markers_absent():
    result = amazon.extract("https://www.amazon.jobs/en/jobs/12345", "<html>unexpected shape</html>")
    assert result.company_name == "Amazon"
    assert result.description is None
    assert result.location is None


def test_extract_none_for_unrelated_page():
    assert amazon.extract("https://example.com/jobs/1", "<html>unrelated</html>") is None
