from datetime import datetime, timedelta, timezone

import pytest

from app.services.adapters import base, gem
from app.services.ats_adapters import board_url_for_key, detect_ats_source, list_job_urls
from tests.conftest import FakeResponse


@pytest.mark.parametrize('url,key', [
    ('https://jobs.gem.com/11x-ai', '11x-ai'),
    ('https://jobs.gem.com/11x-ai/job-id/application?source=test', '11x-ai'),
    ('https://jobs.gem.com/', None),
    ('https://jobs.gem.com/api/public/graphql', None),
    ('https://jobs.gem.com.evil.test/11x-ai', None),
    ('https://example.com/jobs.gem.com/11x-ai', None),
])
def test_match(url, key):
    assert gem._match(url) == key


def test_discovery_filters_dates_deduplicates_and_honors_shared_cap(monkeypatch):
    now = datetime.now(timezone.utc)
    jobs = [
        {'extId': 'old', 'firstPublishedTsSec': (now - timedelta(days=base.RECENT_WINDOW_DAYS)).timestamp()},
        {'extId': 'missing'},
        {'extId': 'invalid', 'firstPublishedTsSec': 'yesterday'},
        {'extId': '../bad', 'firstPublishedTsSec': now.timestamp()},
        {'extId': 'boundary', 'firstPublishedTsSec': (now - timedelta(days=base.RECENT_WINDOW_DAYS - 1)).timestamp()},
        {'extId': 'boundary', 'firstPublishedTsSec': now.timestamp()},
        {'extId': 'today', 'firstPublishedTsSec': now.timestamp()},
        {'extId': 'over-cap', 'firstPublishedTsSec': now.timestamp()},
    ]
    monkeypatch.setattr(base, 'DEFAULT_MAX_JOBS_PER_CRAWL', 2)

    def fake_post(url, json, timeout):
        assert url == 'https://jobs.gem.com/api/public/graphql'
        assert json['variables'] == {'boardId': '11x-ai'}
        assert timeout == base.TIMEOUT
        return FakeResponse(json_data={'data': {'oatsExternalJobPostings': {'jobPostings': jobs}}})

    monkeypatch.setattr(gem, 'post_with_retry', fake_post)
    ats, key = detect_ats_source('https://jobs.gem.com/11x-ai/job-id')
    board = board_url_for_key(ats, key, 'unused')
    assert board == 'https://jobs.gem.com/11x-ai'
    assert list_job_urls(ats, board) == [board + '/boundary', board + '/today']


@pytest.mark.parametrize('body', [{'errors': [{'message': 'Unknown board'}]}, {'data': None},
    {'data': {'oatsExternalJobPostings': None}}, {'data': {'oatsExternalJobPostings': {}}}])
def test_graphql_failures_are_not_treated_as_empty_boards(monkeypatch, body):
    monkeypatch.setattr(gem, 'post_with_retry', lambda *a, **k: FakeResponse(json_data=body))
    with pytest.raises(ValueError):
        gem._fetch_jobs('11x-ai')


def test_empty_board(monkeypatch):
    monkeypatch.setattr(gem, 'post_with_retry', lambda *a, **k: FakeResponse(
        json_data={'data': {'oatsExternalJobPostings': {'jobPostings': []}}}))
    assert gem._fetch_jobs('empty') == []
