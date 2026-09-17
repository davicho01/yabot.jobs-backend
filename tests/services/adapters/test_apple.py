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
