import pytest

from app.services.adapters import homerun
from tests.conftest import FakeResponse


@pytest.mark.parametrize('url,key', [
    ('https://jobs.homerun.co', 'jobs.homerun.co'),
    ('https://acme.homerun.co/head-of-sales/en_GB', 'acme.homerun.co'),
    ('https://www.homerun.co/careers', None),
    ('https://feed.homerun.co/jobs', None),
    ('https://acme.homerun.co.evil.test/', None),
])
def test_match(url, key):
    assert homerun._match(url) == key


def test_fetch_jobs_keeps_one_url_per_posting(monkeypatch):
    locs = [
        'https://acme.homerun.co', 'https://acme.homerun.co/open', 'https://acme.homerun.co/open/en/apply',
        'https://acme.homerun.co/head-of-sales', 'https://acme.homerun.co/head-of-sales/en_GB',
        'https://acme.homerun.co/head-of-sales/nl/apply', 'https://acme.homerun.co/designer',
    ]
    body = ''.join(f'<url><loc>{loc}</loc></url>' for loc in locs)
    response = FakeResponse()
    response.content = f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'.encode()
    monkeypatch.setattr(homerun, 'get_with_retry', lambda url, timeout: response)
    assert homerun._fetch_jobs('acme.homerun.co') == [
        'https://acme.homerun.co/head-of-sales', 'https://acme.homerun.co/designer',
    ]
