from app.services.adapters import paradox
from tests.conftest import FakeResponse

NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
HOST = 'jobs.olivegarden.com'


def _fake_site(monkeypatch, pages: dict[str, str | bytes]):
    def fake_get(url, timeout):
        response = FakeResponse(text=pages[url] if isinstance(pages[url], str) else '')
        if isinstance(pages[url], bytes):
            response.content = pages[url]
        return response

    monkeypatch.setattr(paradox, 'get_with_retry', fake_get)


def test_paged_listing_is_used_when_it_has_job_links(monkeypatch):
    _fake_site(monkeypatch, {
        'https://careers.marriott.com/jobs/page/1': '<a href="/server/job/P1-1-0">x</a>',
        'https://careers.marriott.com/jobs/page/2': '<html></html>',
    })
    assert paradox._fetch_jobs('careers.marriott.com') == ['https://careers.marriott.com/server/job/P1-1-0']


def test_sitemap_fallback_reads_the_jobs_sitemap_newest_first(monkeypatch):
    # Darden's template: /jobs/page/1 is a search form with no job links.
    jobs = (
        f'<urlset {NS}>'
        f'<url><loc>https://{HOST}/search/jobdetails/host/old</loc><lastmod>2026-10-01</lastmod></url>'
        f'<url><loc>https://{HOST}/search/jobdetails/server/new</loc><lastmod>2026-10-07</lastmod></url>'
        '</urlset>'
    ).encode()
    _fake_site(monkeypatch, {
        f'https://{HOST}/jobs/page/1': '<form action="/search/searchjobs"></form>',
        f'https://{HOST}/sitemap.xml': (
            f'<sitemapindex {NS}><sitemap><loc>https://{HOST}/career-site.xml</loc></sitemap>'
            f'<sitemap><loc>https://{HOST}/jobs-site.xml</loc></sitemap></sitemapindex>'
        ).encode(),
        f'https://{HOST}/jobs-site.xml': jobs,
    })
    assert paradox._fetch_jobs(HOST) == [
        f'https://{HOST}/search/jobdetails/server/new',
        f'https://{HOST}/search/jobdetails/host/old',
    ]
