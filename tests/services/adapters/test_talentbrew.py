from app.services.adapters import talentbrew
from tests.conftest import FakeResponse

NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
HOST = 'jobs.mayoclinic.org'


def _sitemap(*entries: tuple[str, str]) -> bytes:
    body = ''.join(f'<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>' for loc, lastmod in entries)
    return f'<urlset {NS}><url><loc>https://{HOST}</loc></url>{body}</urlset>'.encode()


def _fake(monkeypatch, sitemap: bytes, robots: str = 'User-agent: *\nDisallow:/search-jobs/', search_html: str = ''):
    def fake_get(url, timeout, params=None, follow_redirects=False):
        if url.endswith('/sitemap.xml'):
            response = FakeResponse()
            response.content = sitemap
            return response
        assert talentbrew._search_allowed(HOST), 'searched although robots.txt disallows it'
        return FakeResponse(json_data={'results': search_html})

    monkeypatch.setattr(talentbrew, 'get_with_retry', fake_get)
    monkeypatch.setattr(talentbrew.httpx, 'get', lambda url, **kw: FakeResponse(text=robots))


def test_jobs_come_from_the_sitemap_newest_first(monkeypatch):
    _fake(monkeypatch, _sitemap(
        (f'https://{HOST}/job/rochester/rn/33647/1', '2026-10-01T00:00:00Z'),
        (f'https://{HOST}/category/nursing-jobs/33647/8337232/1', '2026-10-07T00:00:00Z'),
        (f'https://{HOST}/en/job/phoenix/desk-operations-specialist/33647/2', '2026-10-07T04:00:00Z'),
    ))
    assert talentbrew._fetch_jobs(HOST) == [
        f'https://{HOST}/en/job/phoenix/desk-operations-specialist/33647/2',
        f'https://{HOST}/job/rochester/rn/33647/1',
    ]


def test_no_search_fallback_where_robots_disallows_it(monkeypatch):
    _fake(monkeypatch, _sitemap())
    assert talentbrew._fetch_jobs(HOST) == []


def test_search_fallback_where_robots_allows_it(monkeypatch):
    _fake(monkeypatch, _sitemap(), robots='User-agent: *\nDisallow:', search_html='<a href="/en/job/x/y/1/2">')
    assert talentbrew._fetch_jobs(HOST) == [f'https://{HOST}/en/job/x/y/1/2']
