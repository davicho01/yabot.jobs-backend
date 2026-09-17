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
