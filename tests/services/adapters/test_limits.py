from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.services import ats_adapters
from app.services.adapters import ADAPTERS, ashby, base, greenhouse
from tests.conftest import FakeResponse


@pytest.mark.parametrize('adapter', ADAPTERS, ids=lambda a: a.ats_type)
def test_every_adapter_is_capped_at_discovery_boundary(monkeypatch, adapter):
    urls = [f'https://example.com/job/{i}' for i in range(510)]
    bounded = replace(adapter, board_key=lambda _: 'board', fetch_jobs=lambda _: urls)
    monkeypatch.setitem(ats_adapters._ADAPTERS_BY_TYPE, adapter.ats_type, bounded)
    assert ats_adapters.list_job_urls(adapter.ats_type, 'https://example.com') == urls[:500]


def test_cap_reads_shared_setting_and_stops_consuming_after_unique_limit(monkeypatch):
    monkeypatch.setattr(base, 'DEFAULT_MAX_JOBS_PER_CRAWL', 2)

    def urls():
        yield 'a'
        yield 'a'
        yield 'b'
        raise AssertionError('read past cap')

    assert base.limit_job_urls(urls()) == ['a', 'b']


@pytest.mark.parametrize('age,expected', [(0, True), (3, True), (4, False), (10, False), (-1, False)])
def test_recent_window_boundaries(monkeypatch, age, expected):
    monkeypatch.setattr(base, 'RECENT_WINDOW_DAYS', 4)
    day = datetime.now(timezone.utc).date() - timedelta(days=age)
    assert base.is_recent_posting(day.isoformat()) is expected


def test_window_reads_shared_setting(monkeypatch):
    monkeypatch.setattr(base, 'RECENT_WINDOW_DAYS', 1)
    today = datetime.now(timezone.utc).date()
    assert base.is_recent_posting(today)
    assert not base.is_recent_posting(today - timedelta(days=1))


def test_dates_normalize_timezone_and_reject_invalid_values():
    assert base.posting_date('2026-09-17T23:30:00-03:00').isoformat() == '2026-09-18'
    assert base.posting_date('invalid') is None
    assert base.posting_date(None) is None


@pytest.mark.parametrize('adapter,url_field,date_field', [
    (greenhouse, 'absolute_url', 'first_published'), (ashby, 'jobUrl', 'publishedAt'),
])
def test_full_board_discovery_keeps_jobs_older_than_recent_window(monkeypatch, adapter, url_field, date_field):
    # Both APIs return the complete current board in one call, so a job that's
    # still open but was posted long ago must still come back — no recency
    # filter should ever be applied here (that's only correct for adapters
    # that paginate a whole board every crawl, see base.RECENT_WINDOW_DAYS).
    today = datetime.now(timezone.utc).date()
    jobs = [{url_field: 'old', date_field: (today - timedelta(days=10)).isoformat()}]
    jobs += [{url_field: f'new-{i}', date_field: today.isoformat()} for i in range(5)]
    monkeypatch.setattr(adapter, 'get_with_retry', lambda *a, **k: FakeResponse(json_data={'jobs': jobs}))
    assert adapter._fetch_jobs('board') == ['old'] + [f'new-{i}' for i in range(5)]
