import json
from datetime import datetime, timedelta, timezone

from app.services.adapters import apple
from app.services.adapters.base import RECENT_WINDOW_DAYS
from tests.conftest import FakeResponse


def _posting(position_id: str, days_ago: int) -> dict:
    day = datetime.now(timezone.utc).date() - timedelta(days=days_ago)
    return {
        "positionId": position_id,
        "transformedPostingTitle": f"role-{position_id}",
        "postingDate": day.strftime("%b %d, %Y"),
    }


def _hydration_page(postings: list[dict]) -> str:
    search_data = {"loaderData": {"search": {"searchResults": postings}}}
    # Mirrors what apple.py expects: JSON.parse of a JS string literal —
    # double-encode the same way _APPLE_HYDRATION_RE's match.group(1) does.
    inner = json.dumps(json.dumps(search_data))[1:-1]
    return f'window.__staticRouterHydrationData = JSON.parse("{inner}");'


def test_fetch_jobs_keeps_postings_within_window_and_stops_at_boundary(monkeypatch):
    postings = [
        _posting("1", 0),
        _posting("2", RECENT_WINDOW_DAYS - 1),
        _posting("3", RECENT_WINDOW_DAYS),
    ]
    calls = []

    def fake_get(url, params, **kwargs):
        calls.append(params["page"])
        return FakeResponse(text=_hydration_page(postings))

    monkeypatch.setattr(apple, "get_with_retry", fake_get)
    urls = apple._fetch_jobs("apple")
    assert urls == [
        apple._APPLE_JOB_URL.format(position_id="1", slug="role-1"),
        apple._APPLE_JOB_URL.format(position_id="2", slug="role-2"),
    ]
    assert calls == [1]


def _job_detail_hydration_page(job_data: dict) -> str:
    data = {"loaderData": {"jobDetails": {"jobsData": job_data}}}
    inner = json.dumps(json.dumps(data))[1:-1]
    return f'window.__staticRouterHydrationData = JSON.parse("{inner}");'


def test_extract_parses_hydration_blob_fields():
    job_data = {
        "postingTitle": "Software Engineer",
        "jobSummary": "Build great things.",
        "responsibilities": "Ship features\nReview code",
        "minimumQualifications": "BS in CS",
        "postingFooters": [{"localizations": {"en_US": [
            {"name": "Pay & Benefits", "content": "<p>Great benefits</p>", "displayOrder": 1},
        ]}}],
        "localeLocation": [{"city": "Cupertino", "stateProvince": "CA", "countryName": "USA"}],
        "postingDateMeta": "2026-03-05T00:00:00Z",
    }
    html = _job_detail_hydration_page(job_data)
    result = apple.extract("https://jobs.apple.com/en-us/details/12345/software-engineer", html)
    assert result.title == "Software Engineer"
    assert result.company_name == "Apple"
    assert result.location == "Cupertino, CA, USA"
    assert result.posted_at.isoformat() == "2026-03-05"
    assert "Build great things." in result.description
    assert "- Ship features" in result.description
    assert "- Review code" in result.description
    assert "BS in CS" in result.description
    assert "Great benefits" in result.description


def test_extract_company_name_set_even_when_hydration_blob_missing():
    # Same page shape change that would break the blob parser shouldn't
    # also silently drop the one thing this site is known for regardless —
    # company_name is gated on the URL, not on the blob's own success.
    result = apple.extract("https://jobs.apple.com/en-us/details/12345/software-engineer", "<html></html>")
    assert result.company_name == "Apple"
    assert result.title is None
    assert result.location is None


def test_extract_none_for_non_apple_url_without_hydration_blob():
    assert apple.extract("https://example.com/jobs/1", "<html></html>") is None
