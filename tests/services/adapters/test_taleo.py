from html import escape
from urllib.parse import quote

from app.services.adapters import base, taleo

URL = "https://drhorton.taleo.net/careersection/2/jobdetail.ftl?job=2605100&lang=en"
DESCRIPTION = "<p>" + "Help buyers find their new home. " * 15 + "</p>"
QUALIFICATIONS = "<ul><li>Sales experience\\: 2 years</li></ul>"


def _page(fields: list[str]) -> str:
    # Taleo's hidden input: "!|!"-separated, URL-escaped, then HTML-escaped.
    value = escape("!|!".join(quote(field, safe="") for field in fields))
    return f'<title>Careers</title><input type="hidden" name="initialHistory" id="initialHistory" value="{value}" />'


FIELDS = [
    "ftlx0", "descRequisition", "true",
    "Submission for the position\\: Online Sales Concierge - (Job Number\\: 2605100)",
    "Online Sales Concierge", "2605100",
    "!*!" + DESCRIPTION, "!*!" + DESCRIPTION,
    "!*!" + QUALIFICATIONS, "!*!" + QUALIFICATIONS,
    "Sales", "Full-time", "Oklahoma-Edmond", "Oklahoma-Edmond", "Home Builder",
]


def _scan(monkeypatch, html):
    monkeypatch.setattr(base, "fetch_html", lambda u: base.FetchedPage(text=html, url=u))
    monkeypatch.setattr(taleo, "fetch_rendered_html", lambda *a, **k: None)
    return taleo.scan_job_url(URL)


def test_posting_is_read_from_the_raw_page(monkeypatch):
    result = _scan(monkeypatch, _page(FIELDS))

    assert result.success and result.title == "Online Sales Concierge"
    assert result.description.count("Help buyers find their new home.") == 15  # duplicate field not repeated
    assert "Sales experience: 2 years" in result.description  # "\:" unescaped
    assert result.location == "Edmond, Oklahoma"  # not "Full-time"


def test_us_state_code_location_is_kept_as_is(monkeypatch):
    fields = [f if f != "Oklahoma-Edmond" else "US-GA-Atlanta" for f in FIELDS]
    assert _scan(monkeypatch, _page(fields)).location == "US-GA-Atlanta"


def test_page_without_the_blob_falls_back_to_rendering(monkeypatch):
    result = _scan(monkeypatch, "<title>Careers</title>")

    assert result.success is False  # render unavailable in this test
    assert "rendering" in result.error
