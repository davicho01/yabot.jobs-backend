import json

import pytest

from app.services.adapters import join
from app.services.ats_adapters import board_url_for_key, detect_ats_source
from tests.conftest import FakeResponse


@pytest.mark.parametrize('url,key', [
    ('https://join.com/companies/mazars', 'mazars'),
    ('https://join.com/companies/Mazars/15000000-senior-audit?pid=1', 'mazars'),
    ('https://join.com/companies', None),
    ('https://join.com.evil.test/companies/mazars', None),
])
def test_match(url, key):
    assert join._match(url) == key


def test_board_url_is_stored_verbatim():
    url = 'https://join.com/companies/mazars/15000000-senior-audit'
    ats_type, key = detect_ats_source(url)
    assert (ats_type, board_url_for_key(ats_type, key, url)) == ('join', url)


def _page(ids, page_count):
    state = {'jobs': {'items': [{'idParam': i} for i in ids], 'pagination': {'pageCount': page_count}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps({"props": {"pageProps": {"initialState": state}}})}</script>'


def test_fetch_jobs_follows_page_count(monkeypatch):
    pages = {1: _page(['1-a', '2-b'], 2), 2: _page(['3-c'], 2)}
    requested = []

    def fake_get(url, params, timeout):
        requested.append(params['page'])
        return FakeResponse(text=pages[params['page']])

    monkeypatch.setattr(join, 'get_with_retry', fake_get)
    assert join._fetch_jobs('mazars') == [f'https://join.com/companies/mazars/{i}' for i in ('1-a', '2-b', '3-c')]
    assert requested == [1, 2]
