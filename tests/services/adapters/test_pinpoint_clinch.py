import pytest

from app.services.adapters import clinch, pinpoint
from app.services.ats_adapters import detect_ats_source
from tests.conftest import FakeResponse


@pytest.mark.parametrize('url,key', [
    ('https://premierleague.pinpointhq.com', 'premierleague.pinpointhq.com'),
    ('https://made-tech.pinpointhq.com/en/postings/78ebb26e', 'made-tech.pinpointhq.com'),
    ('https://www.pinpointhq.com/', None),
    ('https://pinpointhq.com/', None),
    ('https://a.b.pinpointhq.com/', None),
    ('https://premierleague.pinpointhq.com.evil.test/', None),
    ('https://careers.manutd.com/', None),
])
def test_pinpoint_hosted_boards_match_statically(url, key):
    assert pinpoint._match_hosted(url) == key


def test_hosted_pinpoint_board_is_detected_without_network():
    assert detect_ats_source('https://londonhireltd.pinpointhq.com') == ('pinpoint', 'londonhireltd.pinpointhq.com')


def _sitemap(*locs: str) -> bytes:
    body = ''.join(f'<url><loc>{loc}</loc></url>' for loc in locs)
    return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'.encode()


def _detect_with_sitemap(monkeypatch, page_url: str, locs: list[str]):
    monkeypatch.setattr(clinch.httpx, 'get', lambda url, **kw: FakeResponse(text='<html></html>', url=page_url))

    def fake_get(url, timeout):
        response = FakeResponse()
        response.content = _sitemap(*locs)
        return response

    monkeypatch.setattr(clinch, 'get_with_retry', fake_get)
    return clinch._detect_embedded(page_url)


def test_sitemap_fallback_still_accepts_a_real_single_segment_tenant(monkeypatch):
    host = 'careers.upstart.com'
    locs = [f'https://{host}/', f'https://{host}/jobs/senior-engineer-1a2b']
    assert _detect_with_sitemap(monkeypatch, f'https://{host}/jobs/senior-engineer-1a2b', locs) == host


def test_sitemap_fallback_rejects_neogov_style_nested_job_paths(monkeypatch):
    locs = ['https://www.governmentjobs.com/jobs/98681-1/social-work-supervisor-iii']
    assert _detect_with_sitemap(monkeypatch, 'https://www.governmentjobs.com/jobs/5415930-0/x', locs) is None


def test_sitemap_fallback_rejects_a_sitemap_served_from_another_host(monkeypatch):
    # schooljobs.com serves governmentjobs.com's sitemap, so none of its job
    # URLs are on the host we asked about.
    locs = ['https://www.governmentjobs.com/jobs/98681-1/social-work-supervisor-iii']
    assert _detect_with_sitemap(monkeypatch, 'https://www.schooljobs.com/careers/x/jobs/1-0/y', locs) is None


def test_crawling_stays_lenient_for_existing_rows(monkeypatch):
    # Existing iCIMS-hosted rows labeled clinch use /jobs/{id}/{slug}/job.
    locs = ['https://careers-aerotek.icims.com/jobs/1234/some-role/job', 'https://careers-aerotek.icims.com/about']

    def fake_get(url, timeout):
        response = FakeResponse()
        response.content = _sitemap(*locs)
        return response

    monkeypatch.setattr(clinch, 'get_with_retry', fake_get)
    assert clinch._fetch_jobs('careers-aerotek.icims.com') == [locs[0]]
