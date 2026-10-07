import pytest

from app.services.adapters import base, jobaps
from tests.conftest import FakeResponse


@pytest.mark.parametrize('url,key', [
    ('https://www.jobapscloud.com/MD/', 'md'),
    ('https://jobapscloud.com/sjq/', 'sjq'),
    ('https://www.jobapscloud.com/CT/sup/bulpreview.asp?R1=211210&R2=6645SH&R3=001', 'ct'),
    ('https://www.jobapscloud.com/oec/NewHaven/Jobs/Bulletin?R1=2601&R2=1000&R3=01', 'oec/newhaven'),
    ('https://www.jobapscloud.com.evil.test/MD/', None),
])
def test_match(url, key):
    assert jobaps._match(url) == key


BOARD = '''
<a href="/MD/sup/bulpreview.asp?R1=00&amp;R2=PR0000&amp;R3=001" class="JobTitle">Practice Application</a>
<a href="/MD/sup/bulpreview.asp?R1=AF&amp;R2=000000&amp;R3=001" class="JobTitle">Application On-File</a>
<a href="https://www.jobapscloud.com/MD/sup/bulpreview.asp?b=&amp;R1=26&amp;R2=000960&amp;R3=0001">Tax Attorney</a>
<a href="https://www.jobapscloud.com/MD/sup/bulpreview.asp?b=&amp;R1=26&amp;R2=000960&amp;R3=0001">26-000960-0001</a>
<a href="/MIL/sup/bulpreview.asp?R1=2608&amp;R2=2413&amp;R3=001">FIRE CADET</a>
'''


def test_fetch_jobs_dedupes_and_skips_pseudo_postings(monkeypatch):
    monkeypatch.setattr(jobaps, 'get_with_retry', lambda url, **kw: FakeResponse(text=BOARD))
    assert jobaps._fetch_jobs('md') == [
        'https://www.jobapscloud.com/MD/sup/bulpreview.asp?R1=26&R2=000960&R3=0001',
        'https://www.jobapscloud.com/MIL/sup/bulpreview.asp?R1=2608&R2=2413&R3=001',
    ]


JOB = 'https://www.jobapscloud.com/MIL/sup/bulpreview.asp?R1=2608&R2=2413&R3=001'


def _scan(monkeypatch, html):
    monkeypatch.setattr(base, 'fetch_html', lambda url: base.FetchedPage(text=html, url=url))
    return jobaps._scan_job_url(JOB)


def test_a_page_without_json_ld_is_read_from_its_title_and_bulletin(monkeypatch):
    html = ('<title>\r\nJob Announcement: FIRE CADET (Ages 17-19) - City of Milwaukee</title>'
            '<div id="JobBulletinBody"><p>Serve the city.</p></div><div id="contentFooter"></div>')
    result = _scan(monkeypatch, html)
    assert (result.success, result.title, result.company_name) == (True, 'FIRE CADET (Ages 17-19)', 'City of Milwaukee')
    assert 'Serve the city.' in result.description


def test_san_joaquin_announcement_shape_is_read_from_the_bulletin_title(monkeypatch):
    # Live San Joaquin County postings were all marked expired (and closed)
    # before this shape was handled.
    html = ('<title>Announcement: Ag Biologist/Standards Inspector IV - San Joaquin County</title>'
            '<div class="JobBulletinTitle">Ag Biologist/Standards Inspector IV\r\n</div>'
            '<div id="JobBulletinBody"><p>Inspect crops.</p></div></body>')
    result = _scan(monkeypatch, html)
    assert (result.success, result.title, result.company_name) == (
        True, 'Ag Biologist/Standards Inspector IV', 'San Joaquin County'
    )
    assert 'Inspect crops.' in result.description


def test_an_unrecognized_page_is_left_to_the_generic_scanner_not_expired(monkeypatch):
    assert _scan(monkeypatch, '<title>Some New JobAps Layout</title>') is None


def test_an_empty_announcement_is_expired(monkeypatch):
    result = _scan(monkeypatch, '<title>Job Announcement:  - City of Milwaukee</title>')
    assert (result.success, result.expired) == (False, True)


def test_a_json_ld_page_is_left_to_the_generic_scanner(monkeypatch):
    html = '<script type="application/ld+json">{"@type": "JobPosting", "title": "Tax Attorney"}</script>'
    assert _scan(monkeypatch, html) is None
