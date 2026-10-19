"""Tests for app.services.google_indexing — no real Google calls: requests go
to an httpx.MockTransport, state to a LocalDirPageStore in tmp_path."""

import json
from datetime import date, datetime, timezone

import httpx
import pytest

from app.services import google_indexing as gi
from app.services import page_store
from app.services.page_store import LocalDirPageStore

# 9am Eastern on a weekday: the first of the day's 7 scheduled runs.
FIRST_RUN = datetime(2026, 10, 19, 13, 0, tzinfo=timezone.utc)
# 9pm Eastern: the last one.
LAST_RUN = datetime(2026, 10, 20, 1, 0, tzinfo=timezone.utc)
DAY = date(2026, 10, 19)


@pytest.fixture
def store(tmp_path, monkeypatch):
    local = LocalDirPageStore(tmp_path)
    monkeypatch.setattr(page_store, "_store", local)
    monkeypatch.setattr(gi.settings, "google_indexing_daily_quota", 200)
    return local


def _client(sent: list, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(status, json={})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _pages(n: int, score: int = 0, day: date = DAY) -> list:
    return [(score, day, f"jobs/us/x/{day}/job-{score}-{i}") for i in range(n)]


def _notify(new, removed=(), now=FIRST_RUN, status=200):
    sent = []
    result = gi.notify(new, removed, now=now, client=_client(sent, status), token=lambda: "t")
    return result, sent


def test_first_run_spends_its_share_of_the_day(store):
    result, sent = _notify(_pages(100))
    assert result.updated == len(sent) == 29  # ceil(200 / 7)
    assert result.left_today == 171
    assert sent[0] == {"url": "https://yabot.jobs/jobs/us/x/2026-10-19/job-0-0", "type": "URL_UPDATED"}


def test_later_runs_split_what_is_left_and_the_last_takes_it_all(store):
    _notify(_pages(100))
    result, sent = _notify(_pages(500), now=LAST_RUN)
    assert len(sent) == 171 and result.left_today == 0
    assert _notify(_pages(10), now=LAST_RUN)[1] == []


def test_a_new_quota_day_starts_from_zero(store):
    store.put(gi.STATE_KEY, json.dumps({"day": "2026-10-18", "sent": 200}), "application/json")
    assert len(_notify(_pages(100))[1]) == 29


def test_highest_score_then_newest_first_then_removed(store):
    new = _pages(1, score=0) + _pages(1, score=3, day=date(2026, 10, 17)) + _pages(1, score=3)
    _, sent = _notify(new, removed=["job/gone"], now=LAST_RUN)
    assert [(s["url"].rsplit("/", 1)[1], s["type"]) for s in sent] == [
        ("job-3-0", "URL_UPDATED"),  # score 3, Oct 19
        ("job-3-0", "URL_UPDATED"),  # score 3, Oct 17
        ("job-0-0", "URL_UPDATED"),
        ("gone", "URL_DELETED"),
    ]
    assert "2026-10-19" in sent[0]["url"] and "2026-10-17" in sent[1]["url"]


def test_removed_pages_only_with_budget_to_spare(store):
    _, sent = _notify(_pages(40), removed=["job/gone"])
    assert all(s["type"] == "URL_UPDATED" for s in sent)


def test_429_stops_and_marks_the_day_spent(store):
    result, sent = _notify(_pages(10), status=429)
    assert (len(sent), result.stopped, result.left_today) == (1, "quota", 0)
    assert _notify(_pages(10), now=LAST_RUN)[1] == []


def test_403_stops_without_spending(store):
    result, sent = _notify(_pages(10), status=403)
    assert (len(sent), result.stopped, result.left_today) == (1, "forbidden", 200)


def test_nothing_to_send_makes_no_calls(store):
    def no_token():
        raise AssertionError("asked for a token with nothing to send")

    result = gi.notify([], [], now=FIRST_RUN, token=no_token)
    assert (result.updated, store.get(gi.STATE_KEY)) == (0, None)
