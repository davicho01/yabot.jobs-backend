import json

import pytest

from app.services.adapters import base, paycom

KEY = '6730889B39B617F0B2F91A93C50877B2'
PORTAL_JOB = f'https://www.paycomonline.net/v4/ats/web.php/portal/{KEY}/jobs/602656'


@pytest.mark.parametrize('url,portal', [
    (PORTAL_JOB, PORTAL_JOB),
    (f'https://paycomonline.net/v4/ats/web.php/portal/{KEY.lower()}/jobs/602656', PORTAL_JOB),
    # The disallowed legacy shape is fetched at its (allowed) portal equivalent.
    (f'https://www.paycomonline.net/v4/ats/web.php/jobs/ViewJobDetails?job=602656&clientkey={KEY}', PORTAL_JOB),
    (f'https://www.paycomonline.net/v4/ats/web.php/portal/{KEY}/career-page', None),
    ('https://boards.greenhouse.io/acme/jobs/1', None),
])
def test_portal_url(url, portal):
    assert paycom._portal_url(url) == portal


def _scan(monkeypatch, html, url=PORTAL_JOB):
    fetched = []

    def fake_fetch(u):
        fetched.append(u)
        return base.FetchedPage(text=html, url=u)

    monkeypatch.setattr(base, 'fetch_html', fake_fetch)
    return paycom.scan_job_url(url), fetched


def test_a_live_posting_is_read_from_json_ld(monkeypatch):
    job = {'@type': 'JobPosting', 'title': 'Part-Time Leasing Consultant', 'description': 'Lease apartments.',
           'responsibilities': 'Give tours.', 'hiringOrganization': {'name': 'PeakMade'}, 'datePosted': '2026-09-28'}
    html = f'<title>Loading...</title><script type="application/ld+json">{json.dumps(job)}</script>'
    legacy = f'https://www.paycomonline.net/v4/ats/web.php/jobs/ViewJobDetails?job=602656&clientkey={KEY}'
    result, fetched = _scan(monkeypatch, html, legacy)
    assert fetched == [PORTAL_JOB]
    assert (result.success, result.title, result.company_name, str(result.posted_at)) == (
        True, 'Part-Time Leasing Consultant', 'PeakMade', '2026-09-28'
    )
    assert 'Lease apartments.' in result.description and 'Give tours.' in result.description


def test_the_shell_without_a_posting_is_expired(monkeypatch):
    result, _ = _scan(monkeypatch, '<title>Loading...</title><div id="root"></div>')
    assert (result.success, result.expired) == (False, True)


def test_a_non_job_paycom_url_fails_without_expiring_or_fetching(monkeypatch):
    result, fetched = _scan(monkeypatch, '', f'https://www.paycomonline.net/v4/ats/web.php/portal/{KEY}/career-page')
    assert (result.success, result.expired, fetched) == (False, False, [])


def test_other_hosts_are_not_claimed():
    assert paycom.scan_job_url('https://boards.greenhouse.io/acme/jobs/1') is None
