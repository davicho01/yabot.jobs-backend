import json

import pytest

from app.services.adapters import base, dayforce
from app.services.ats_adapters import board_url_for_key, detect_ats_source
from tests.conftest import FakeResponse

BOARD = 'https://jobs.dayforcehcm.com/en-US/select/CANDIDATEPORTAL'
JOB = f'{BOARD}/jobs/33214'


@pytest.mark.parametrize('url,key', [
    (BOARD, 'select/CANDIDATEPORTAL'),
    (JOB, 'select/CANDIDATEPORTAL'),
    ('https://jobs.dayforcehcm.com/select/CANDIDATEPORTAL?page=2', 'select/CANDIDATEPORTAL'),
    ('https://jobs.dayforcehcm.com.evil.test/en-US/select/CANDIDATEPORTAL', None),
])
def test_match(url, key):
    assert dayforce._match(url) == key


def test_board_url_is_stored_verbatim():
    ats_type, key = detect_ats_source(JOB)
    assert ats_type == 'dayforce'
    assert board_url_for_key(ats_type, key, JOB) == JOB


class _Client:
    def __init__(self, pages):
        self.pages, self.posts = pages, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url):
        return FakeResponse(json_data={'csrfToken': 'tok'})

    def post(self, url, json, headers):
        assert headers == {'X-CSRF-TOKEN': 'tok'}
        self.posts.append(json['paginationStart'])
        return FakeResponse(json_data=self.pages[json['paginationStart']])


def test_fetch_jobs_pages_through_the_search_api(monkeypatch):
    client = _Client({
        0: {'maxCount': 3, 'jobPostings': [{'jobPostingId': 1}, {'jobPostingId': 2}]},
        2: {'maxCount': 3, 'jobPostings': [{'jobPostingId': 3}]},
    })
    monkeypatch.setattr(dayforce.httpx, 'Client', lambda **kw: client)
    assert dayforce._fetch_jobs('select/CANDIDATEPORTAL') == [f'{BOARD}/jobs/{i}' for i in (1, 2, 3)]
    assert client.posts == [0, 2]


def _job_page(**job):
    data = {'props': {'pageProps': {
        'jobData': {'jobTitle': 'Ticketing Coordinator', 'postingStatus': 1, 'postingStartTimestampUTC': '2026-10-06T05:00:00+00:00',
                    'jobPostingContent': {'jobDescription': '<p>Coordinate tickets.</p>'},
                    'postingLocations': [{'formattedAddress': 'Big Spring, TX 79720, USA'}], **job},
        'dehydratedState': {'queries': [{'queryKey': ['site-info'], 'state': {'data': {'candidateCorrespondenceClientName': 'Select Water Solutions'}}}]},
    }}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def test_scan_reads_next_data(monkeypatch):
    monkeypatch.setattr(base, 'fetch_html', lambda url: base.FetchedPage(text=_job_page(), url=url))
    result = dayforce._scan_job_url(JOB)
    assert (result.success, result.title, result.company_name, result.location, str(result.posted_at)) == (
        True, 'Ticketing Coordinator', 'Select Water Solutions', 'Big Spring, TX 79720, USA', '2026-10-06'
    )
    assert 'Coordinate tickets.' in result.description


def test_a_closed_posting_scans_as_expired(monkeypatch):
    monkeypatch.setattr(base, 'fetch_html', lambda url: base.FetchedPage(text=_job_page(postingStatus=6), url=url))
    result = dayforce._scan_job_url(JOB)
    assert (result.success, result.expired) == (False, True)


def test_board_urls_are_not_scanned():
    assert dayforce._scan_job_url(BOARD) is None
