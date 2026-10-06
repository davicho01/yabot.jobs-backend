from app.services.adapters import applitrack, base
from tests.conftest import FakeResponse

# Trimmed from live Output.asp feeds: postings arrive as document.write()
# string literals, so quotes are backslash-escaped and images are inlined.
_FEED = (
    "document.write('<ul class=\\'postingsList\\' id=\\'p26723_\\'><table class=\\'title\\'><tr>"
    "<td id=\\'wrapword\\' style=\\'x\\'>Granite School District Administrator Pool</td>"
    "<td><span class=\\'title2\\'> JobID: 26723 <input onclick=\"applyFor(\\'26723\\',\\'Administration\\',\\'\\')\" /></span></td></tr></table>"
    "<div><li><span class=\\'label\\'>Position Type:</span><br/><span class=\\'normal\\'>Administration</span></li>"
    "<li><span class=\"label\" >Date Posted:</span><br/><span class=\"normal\">10/1/2026</span></li>"
    "<span class=\"normal\"><p><img src=\"data:image/png;base64,iVBORw0KGgo=\"/>Lead a school.</p><ul><li>Hire staff.</li></ul></span>"
    "</div></ul>')"
)
_PAGE = (
    "<title>Granite School District Administrator Pool - Frontline Recruitment</title>"
    '<ul id="contactInfo" class="menu"><li><a href=\'http://www.graniteschools.org\'>Granite School District</a></li>'
    "<li><a href='https://maps.google.com/?q=loc:2500 S. State Street  Salt Lake City, UT 84115'>2500 S. State Street </a>"
    "<li>Salt Lake City, UT 84115</li><li><a href='https://app.frontlineeducation.com'>Admin Login</a></ul>"
)
_JOB_URL = "https://www.applitrack.com/graniteschools/onlineapp/default.aspx?AppliTrackJobID=26723"


def _serve_feed(monkeypatch, feed):
    monkeypatch.setattr(applitrack, "get_with_retry", lambda url, params, timeout: FakeResponse(text=feed))


def test_match_reads_client_from_board_and_job_urls():
    assert applitrack._match(_JOB_URL) == "graniteschools"
    assert applitrack._match("https://www.applitrack.com/AlpineSchools/onlineapp/") == "alpineschools"
    assert applitrack._match("https://www.applitrack.com/") is None


def test_fetch_jobs_reads_job_ids_from_feed(monkeypatch):
    _serve_feed(monkeypatch, _FEED + _FEED.replace("26723", "26724"))
    assert applitrack._fetch_jobs("graniteschools") == [_JOB_URL, _JOB_URL.replace("26723", "26724")]


def test_scan_combines_feed_posting_and_footer_district(monkeypatch):
    _serve_feed(monkeypatch, _FEED)
    monkeypatch.setattr(base, "fetch_html", lambda u: base.FetchedPage(text=_PAGE, url=u))
    result = applitrack.scan_job_url(_JOB_URL)
    assert result.success
    assert result.title == "Granite School District Administrator Pool"
    assert result.company_name == "Granite School District"
    assert result.location == "Salt Lake City, UT"
    assert result.posted_at.isoformat() == "2026-10-01"
    assert "Lead a school." in result.description and "Hire staff." in result.description
    assert "base64" not in result.full_html


def test_scan_flags_missing_posting_as_expired(monkeypatch):
    _serve_feed(monkeypatch, "document.write('<p>Openings as of 10/6/2026</p>')")
    monkeypatch.setattr(base, "fetch_html", lambda u: base.FetchedPage(text=_PAGE, url=u))
    result = applitrack.scan_job_url(_JOB_URL.replace("26723", "1"))
    assert not result.success and result.expired
