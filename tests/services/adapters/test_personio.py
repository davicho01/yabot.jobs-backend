import pytest

from app.services.adapters import personio
from app.services.ats_adapters import board_url_for_key, detect_ats_source, list_job_urls
from tests.conftest import FakeResponse


@pytest.mark.parametrize('url,key', [
    ('https://360t.jobs.personio.de/', '360t'),
    ('https://360t.jobs.personio.de/job/123?language=en', '360t'),
    ('https://ohpen.jobs.personio.com', 'ohpen'),
    ('https://stark.jobs.personio.com/xml', 'stark'),
    ('https://1nce.jobs.personio.com/job/42', '1nce'),
    ('https://personio.de/', None),
    ('https://foo.jobs.personio.de.evil.test/', None),
    ('https://foo.jobs.personio.comx/', None),
])
def test_match_accepts_both_tlds(url, key):
    assert personio._match(url) == key


def test_com_and_de_urls_resolve_to_the_same_board():
    ats, key = detect_ats_source('https://ohpen.jobs.personio.com')
    assert (ats, key) == detect_ats_source('https://ohpen.jobs.personio.de/job/1')
    assert board_url_for_key(ats, key, 'https://ohpen.jobs.personio.com') == 'https://ohpen.jobs.personio.de/'


def test_fetch_jobs_reads_the_de_feed_for_a_com_tenant(monkeypatch):
    xml = b'<workzag-jobs><position><id>7</id></position><position><id>9</id></position><position/></workzag-jobs>'

    def fake(url, timeout):
        assert url == 'https://ohpen.jobs.personio.de/xml'
        response = FakeResponse()
        response.content = xml
        return response

    monkeypatch.setattr(personio, 'get_with_retry', fake)
    assert list_job_urls('personio', 'https://ohpen.jobs.personio.de/') == [
        'https://ohpen.jobs.personio.de/job/7',
        'https://ohpen.jobs.personio.de/job/9',
    ]
