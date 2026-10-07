import json

import pytest

from app.services.adapters import base, cognizant
from tests.conftest import FakeResponse

JOB = 'https://careers.cognizant.com/us-en/jobs/00070879131/healthcare-quality-engineering-lead/'


@pytest.mark.parametrize('url,key', [
    ('https://careers.cognizant.com', 'cognizant'),
    (JOB, 'cognizant'),
    ('https://careers.cognizant.com.evil.test/', None),
    ('https://www.cognizant.com/us/en/careers', None),
])
def test_match(url, key):
    assert cognizant._match(url) == key


def test_fetch_jobs_keeps_us_en_job_pages_newest_first(monkeypatch):
    entries = [
        ('https://careers.cognizant.com/us-en/', '2026-10-07'),
        ('https://careers.cognizant.com/us-en/jobs/00000000001/older/', '2026-10-01'),
        ('https://careers.cognizant.com/global-en/jobs/00000000002/newer/', '2026-10-07'),
        ('https://careers.cognizant.com/us-en/jobs/00000000002/newer/', '2026-10-07'),
    ]
    body = ''.join(f'<url><loc>{loc}</loc><lastmod>{lm}</lastmod></url>' for loc, lm in entries)
    response = FakeResponse()
    response.content = f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'.encode()
    monkeypatch.setattr(cognizant, 'get_with_retry', lambda url, timeout: response)
    assert cognizant._fetch_jobs('cognizant') == [
        'https://careers.cognizant.com/us-en/jobs/00000000002/newer/',
        'https://careers.cognizant.com/us-en/jobs/00000000001/older/',
    ]


def _scan(monkeypatch, html):
    monkeypatch.setattr(base, 'fetch_html', lambda url: base.FetchedPage(text=html, url=url))
    return cognizant._scan_job_url(JOB)


def test_json_ld_with_an_escaped_plus_in_its_type_is_read(monkeypatch):
    job = {'@type': 'JobPosting', 'title': 'Healthcare Quality Engineering Lead', 'description': '<p>Lead QE.</p>',
           'hiringOrganization': {'name': 'Cognizant'}, 'datePosted': '2026-10-07'}
    html = f'<script id="js-job-posting" type="application/ld&#x2B;json">{json.dumps(job)}</script>'
    result = _scan(monkeypatch, html)
    assert (result.success, result.title, result.company_name) == (True, 'Healthcare Quality Engineering Lead', 'Cognizant')
    assert 'Lead QE.' in result.description


def test_a_404_page_is_expired(monkeypatch):
    result = _scan(monkeypatch, '<title>404 | Cognizant Careers</title><h1>404</h1>')
    assert (result.success, result.expired) == (False, True)


def test_an_unrecognized_page_fails_without_expiring(monkeypatch):
    result = _scan(monkeypatch, '<title>Cognizant Careers</title>')
    assert (result.success, result.expired) == (False, False)


def test_non_job_pages_are_not_claimed():
    assert cognizant._scan_job_url('https://careers.cognizant.com/us-en/') is None
