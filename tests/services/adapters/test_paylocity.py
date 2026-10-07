import json

import pytest

from app.services.adapters import paylocity
from app.services.ats_adapters import board_url_for_key, detect_ats_source
from tests.conftest import FakeResponse

GUID = '95433dba-4929-4978-a3d5-a8171cedae6b'
BOARD = f'https://recruiting.paylocity.com/recruiting/jobs/All/{GUID}/GBC-Food-Services'


@pytest.mark.parametrize('url,key', [
    (BOARD, f'recruiting.paylocity.com/{GUID}'),
    (f'https://2000Recruiting.paylocity.com/Recruiting/Jobs/All/{GUID.upper()}', f'2000recruiting.paylocity.com/{GUID}'),
    ('https://recruiting.paylocity.com/Recruiting/Jobs/Details/4356647', None),
    (f'https://recruiting.paylocity.com.evil.test/recruiting/jobs/All/{GUID}', None),
])
def test_match(url, key):
    assert paylocity._match(url) == key


def test_board_url_is_stored_verbatim():
    ats_type, key = detect_ats_source(BOARD)
    assert ats_type == 'paylocity'
    assert board_url_for_key(ats_type, key, BOARD) == BOARD


def test_fetch_jobs_reads_the_embedded_list_newest_first_without_internal_jobs(monkeypatch):
    page_data = {'ModuleTitle': 'GBC Food Services', 'Jobs': [
        {'JobId': 1, 'PublishedDate': '2026-10-01T09:00:00-05:00', 'IsInternal': False},
        {'JobId': 2, 'PublishedDate': '2026-10-05T09:00:00-05:00', 'IsInternal': False},
        {'JobId': 3, 'PublishedDate': '2026-10-06T09:00:00-05:00', 'IsInternal': True},
    ]}
    html = f'<script>window.pageData = {json.dumps(page_data)};</script>'
    monkeypatch.setattr(paylocity, 'get_with_retry', lambda url, **kw: FakeResponse(text=html))
    assert paylocity._fetch_jobs(f'recruiting.paylocity.com/{GUID}') == [
        'https://recruiting.paylocity.com/Recruiting/Jobs/Details/2',
        'https://recruiting.paylocity.com/Recruiting/Jobs/Details/1',
    ]


class _Redirect(FakeResponse):
    def __init__(self, location):
        super().__init__(status_code=302)
        self.headers = {'location': location}
        self.is_redirect = True


def test_a_removed_posting_scans_as_expired(monkeypatch):
    monkeypatch.setattr(paylocity.httpx, 'get', lambda url, **kw: _Redirect('/Recruiting/Jobs/JobNotFound'))
    result = paylocity._scan_job_url('https://2000recruiting.paylocity.com/Recruiting/Jobs/Details/46951')
    assert (result.success, result.expired) == (False, True)


def test_a_live_posting_falls_through_to_the_generic_scanner(monkeypatch):
    live = FakeResponse(text='<html></html>')
    live.is_redirect, live.headers = False, {}
    monkeypatch.setattr(paylocity.httpx, 'get', lambda url, **kw: live)
    assert paylocity._scan_job_url('https://recruiting.paylocity.com/Recruiting/Jobs/Details/4039800') is None
    assert paylocity._scan_job_url('https://boards.greenhouse.io/acme/jobs/1') is None
