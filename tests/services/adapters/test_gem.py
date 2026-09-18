import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services import job_scanner
from app.services.adapters import base, gem
from app.services.ats_adapters import board_url_for_key, detect_ats_source, list_job_urls
from tests.conftest import FakeResponse

SCAN_URL = 'https://jobs.gem.com/11x-ai/job-id'


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


@pytest.mark.parametrize('json_ld', [False, True])
def test_gem_scan_preserves_rich_description_and_all_sections(monkeypatch, json_ld):
    html = '<meta name="description" content="Flattened summary">'
    if json_ld:
        html += '<script type="application/ld+json">' + json.dumps({
            '@type': 'JobPosting', 'title': 'Engineer', 'description': 'Flattened summary',
        }) + '</script>'
    monkeypatch.setattr(base, 'fetch_html', lambda _: FakeResponse(text=html, url=SCAN_URL))

    def post(url, json, timeout):
        assert url == 'https://jobs.gem.com/api/public/graphql'
        assert json['variables'] == {'boardId': '11x-ai', 'extId': 'job-id'}
        return FakeResponse(json_data={'data': {'oatsExternalJobPosting': {
            'descriptionHtml': '<h2><strong>About the Role</strong></h2><h2><br></h2>'
                '<p>Build <b>agents</b>.</p><ul><li>Design systems</li><li>Ship features</li></ul>'
                '<p><em>Equal opportunity employer</em></p>',
            'compensationHtml': '<p>US base salary: $180,000 - $200,000 USD</p>',
            'jobPostSectionHtml': {'introHtml': '<h3>About 11x</h3><p>Company introduction</p>',
                'outroHtml': '<p><a href="https://example.com/legal">Legal notice</a></p>'},
            'locations': [{'name': 'Remote - US', 'isRemote': True}],
            'job': {'employmentType': 'FULL_TIME'},
        }}})

    monkeypatch.setattr(gem.httpx, 'post', post)
    monkeypatch.setattr(gem.httpx, 'get', lambda url, timeout: FakeResponse(
        text='<meta property="og:title" content="11x.ai Careers"/>', url=url))
    result = job_scanner.scan_job_url(SCAN_URL)
    assert result.success
    for text in ['### About 11x', 'Company introduction', '## **About the Role**',
                 'Build **agents**.', '- Design systems\n- Ship features',
                 '*Equal opportunity employer*', '[Legal notice](https://example.com/legal)']:
        assert text in result.description
    assert 'Flattened summary' not in result.description
    assert '\n##\n' not in result.description
    assert (result.salary_min, result.salary_max) == (180000, 200000)
    if not json_ld:
        # Gem never ships JSON-LD in practice, but the JSON-LD branch has no
        # gem-specific wiring at all — only the no-JSON-LD branch (exercised
        # here) is expected to surface these.
        assert result.company_name == '11x.ai'
        assert result.location == 'Remote - US'
        assert result.workplace_type == 'remote'
        assert result.employment_type == 'full_time'


@pytest.mark.parametrize('body', [{'errors': [{'message': 'unavailable'}]}, {'data': None},
    {'data': {'oatsExternalJobPosting': None}}, {'data': {'oatsExternalJobPosting': {'descriptionHtml': None}}}])
def test_gem_api_failure_preserves_metadata_fallback(monkeypatch, body):
    monkeypatch.setattr(base, 'fetch_html', lambda _: FakeResponse(
        text='<meta name="description" content="Existing description">', url=SCAN_URL))
    monkeypatch.setattr(gem.httpx, 'post', lambda *a, **k: FakeResponse(json_data=body))
    assert job_scanner.scan_job_url(SCAN_URL).description == 'Existing description'


def test_gem_scan_http_failure_returns_none(monkeypatch):
    monkeypatch.setattr(gem.httpx, 'post', lambda *a, **k: FakeResponse(status_code=503))
    assert gem._fetch_job_data(SCAN_URL) is None


@pytest.mark.parametrize('url', ['https://example.com/job', 'https://jobs.gem.com/11x-ai',
    'https://jobs.gem.com.evil.test/11x-ai/job-id'])
def test_other_pages_do_not_fetch_gem_scan_api(monkeypatch, url):
    def unexpected(*a, **k):
        raise AssertionError('Unexpected API request')
    monkeypatch.setattr(gem.httpx, 'post', unexpected)
    assert gem._fetch_job_data(url) is None


@pytest.mark.parametrize('url', ['https://example.com/job', 'https://jobs.gem.com.evil.test/11x-ai/job-id'])
def test_non_gem_pages_do_not_fetch_gem_board_page(monkeypatch, url):
    def unexpected(*a, **k):
        raise AssertionError('Unexpected board request')
    monkeypatch.setattr(gem.httpx, 'get', unexpected)
    assert gem._fetch_company_name(url) is None


def test_gem_company_name_strips_careers_suffix(monkeypatch):
    monkeypatch.setattr(gem.httpx, 'get', lambda url, timeout: FakeResponse(
        text='<meta property="og:title" content="Bilt Rewards Careers"/>', url=url))
    assert gem._fetch_company_name(SCAN_URL) == 'Bilt Rewards'


def test_gem_company_name_board_http_failure_returns_none(monkeypatch):
    monkeypatch.setattr(gem.httpx, 'get', lambda *a, **k: FakeResponse(status_code=503))
    assert gem._fetch_company_name(SCAN_URL) is None
