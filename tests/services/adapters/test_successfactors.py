from app.services.adapters import base, successfactors
from tests.conftest import FakeResponse

_SIGNED_EMPTY_SEARCH = '<script src="https://rmkcdn.successfactors.com/x.js"></script><div id="searchResults"></div>'
_RSS_FEED = """<rss><channel>
<item><title>Mechanic</title><link>https://jobs.crh.com/job/Smithfield-Mechanic-UT-84335/1437302633/</link></item>
<item><title>Driver</title><link>https://jobs.crh.com/job/Tarrant-Driver-AL-35217/1444976733/</link></item>
<item><title>Elsewhere</title><link>https://other.example.com/job/X/1999999999/</link></item>
</channel></rss>"""
_URLSET_FEED = """<urlset><url><loc>https://jobs.nutrien.com/North-America/job/Operator/32466-en_US/</loc></url>
<url><loc>https://jobs.nutrien.com/North-America/job/Operator/32466-fr_CA/</loc></url>
<url><loc>https://jobs.nutrien.com/North-America/about/</loc></url></urlset>"""


def _serve(monkeypatch, pages):
    calls = []

    def fake_get(url, timeout, params=None):
        calls.append((url, (params or {}).get("startrow")))
        body = pages[url] if url in pages else pages[(url, params["startrow"])]
        return FakeResponse(text=body)

    monkeypatch.setattr(successfactors, "get_with_retry", fake_get)
    return calls


def test_falls_back_to_rss_feed_when_search_renders_client_side(monkeypatch):
    _serve(monkeypatch, {("https://jobs.crh.com/search/", 0): _SIGNED_EMPTY_SEARCH, "https://jobs.crh.com/sitemap.xml": _RSS_FEED})
    # Same-host postings only, newest (highest requisition id) first.
    assert successfactors._fetch_jobs("jobs.crh.com") == [
        "https://jobs.crh.com/job/Tarrant-Driver-AL-35217/1444976733/",
        "https://jobs.crh.com/job/Smithfield-Mechanic-UT-84335/1437302633/",
    ]


def test_urlset_feed_keeps_one_url_per_requisition(monkeypatch):
    _serve(monkeypatch, {("https://jobs.nutrien.com/search/", 0): _SIGNED_EMPTY_SEARCH, "https://jobs.nutrien.com/sitemap.xml": _URLSET_FEED})
    assert successfactors._fetch_jobs("jobs.nutrien.com") == ["https://jobs.nutrien.com/North-America/job/Operator/32466-en_US/"]


def test_feed_fallback_requires_the_successfactors_signature(monkeypatch):
    calls = _serve(monkeypatch, {("https://example.com/search/", 0): "<html>no jobs here</html>"})
    assert successfactors._fetch_jobs("example.com") == []
    assert ("https://example.com/sitemap.xml", None) not in calls


def test_search_paging_advances_by_actual_page_size_and_stops_on_repeat(monkeypatch):
    def page(ids):
        return "successfactors " + "".join(f'<a href="/job/Role-{i}/{i}/">x</a>' for i in ids)

    calls = _serve(monkeypatch, {
        ("https://careers.example.com/search/", 0): page(range(1, 11)),
        ("https://careers.example.com/search/", 10): page(range(11, 14)),
        # A startrow past the end re-serves the last page.
        ("https://careers.example.com/search/", 13): page(range(11, 14)),
    })
    urls = successfactors._fetch_jobs("careers.example.com")
    assert len(urls) == 13 and [row for _, row in calls] == [0, 10, 13]


def test_scan_reads_career_site_builder_layout(monkeypatch):
    html = """<span lang="en-US" itemprop="title" class="rtltextaligneligible">Mechanic
    </span><span class="rtltextaligneligible">AMAT</span>
    <span xml:lang="en-US" class="rtltextaligneligible">Smithfield, Utah, United States
    </span><span class="joblayouttoken-label">Posting Start Date:</span>
    <span class="rtltextaligneligible">9/15/26</span>
    <span itemprop="description" class="rtltextaligneligible"><p>Position <span>Overview</span></p></span>
    <span itemprop="description" class="rtltextaligneligible"><p>What CRH Offers You</p></span>"""
    monkeypatch.setattr(base, "fetch_html", lambda u: base.FetchedPage(text=html, url=u))
    result = successfactors.scan_job_url("https://jobs.crh.com/job/Smithfield-Mechanic-UT-84335/528892-en_US/")
    assert result.title == "Mechanic"
    assert result.location == "Smithfield, Utah, United States"
    assert result.posted_at.isoformat() == "2026-09-15"
    assert "Position Overview" in result.description and "What CRH Offers You" in result.description
