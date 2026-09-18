from datetime import date

import pytest

from app.services.adapters import greenhouse
from tests.conftest import FakeResponse


def test_extract_embedded_widget_returns_structured_fields(monkeypatch):
    html = (
        '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>'
        '<a href="https://boards.greenhouse.io/embed/job_app?for=acme&token=123&gh_jid=555">Apply</a>'
    )
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        return FakeResponse(json_data={
            "title": "Staff Engineer",
            "company_name": "Acme Inc",
            "location": {"name": "Remote - US"},
            "metadata": [{"name": "Workplace Type", "value": "Remote"}],
            "first_published": "2026-01-02T00:00:00Z",
            "content": "&lt;p&gt;Build things.&lt;/p&gt;",
        })

    monkeypatch.setattr(greenhouse.httpx, "get", fake_get)
    result = greenhouse.extract("https://acme.com/careers?gh_jid=555", html)
    assert result.title == "Staff Engineer"
    assert result.company_name == "Acme Inc"
    assert result.location == "Remote - US"
    assert result.workplace_type == "remote"
    assert result.posted_at == date(2026, 1, 2)
    assert result.description == "Build things."
    assert calls == [("https://boards-api.greenhouse.io/v1/boards/acme/jobs/555", {"content": "true"})]


def test_extract_embedded_widget_guesses_slug_from_domain_when_no_token(monkeypatch):
    # The Coalition case (see greenhouse.py's own comment): no embed script
    # anywhere, but gh_jid leaks through some other serialized field on the
    # page — the board slug then has to be guessed from the domain and
    # verified via the job endpoint itself.
    html = '<script>var data = {"absolute_url": "https://boards.greenhouse.io/coalition/jobs/777?gh_jid=777"};</script>'
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if url.endswith("/coalitioninc/jobs/777"):
            return FakeResponse(status_code=404)
        return FakeResponse(json_data={
            "title": "Engineer", "company_name": "Coalition", "location": None,
            "metadata": [], "content": None,
        })

    monkeypatch.setattr(greenhouse.httpx, "get", fake_get)
    result = greenhouse.extract("https://coalitioninc.com/careers/123", html)
    assert result.title == "Engineer"
    assert result.company_name == "Coalition"
    assert len(calls) == 2


def test_extract_embedded_widget_none_when_every_slug_guess_fails(monkeypatch):
    html = '<script>var data = {"absolute_url": "https://boards.greenhouse.io/coalition/jobs/777?gh_jid=777"};</script>'
    monkeypatch.setattr(greenhouse.httpx, "get", lambda *a, **k: FakeResponse(status_code=404))
    assert greenhouse.extract("https://coalitioninc.com/careers/123", html) is None


def test_extract_job_board_template_uses_remix_blob_and_description_div():
    # job-boards.greenhouse.io's newer template: no embed widget at all
    # (no gh_jid anywhere), so this falls through to scraping the
    # window.__remixContext blob and the job__description div directly.
    html = (
        'window.__remixContext = {"state":{"loaderData":{"root":{'
        '"company_name":"Acme Inc","job_post_location":"Remote - US",'
        '"published_at":"2026-01-02T10:00:00-04:00"}}}};'
        '<div class="wrapper"><div class="job__description body"><p>Build <b>things</b>.</p></div></div>'
    )
    result = greenhouse.extract("https://job-boards.greenhouse.io/acme/jobs/1", html)
    assert result.company_name == "Acme Inc"
    assert result.location == "Remote - US"
    assert result.posted_at == date(2026, 1, 2)
    assert result.description == "Build **things**."


def test_extract_job_board_template_partial_data_still_returns_result():
    html = 'window.__remixContext = {"company_name":"Acme Inc"};'
    result = greenhouse.extract("https://job-boards.greenhouse.io/acme/jobs/1", html)
    assert result.company_name == "Acme Inc"
    assert result.location is None
    assert result.description is None
    assert result.posted_at is None


def test_extract_none_when_nothing_matches():
    assert greenhouse.extract("https://example.com/jobs/1", "<html><body>Nothing here</body></html>") is None


@pytest.mark.parametrize("url,expected", [
    ("https://job-boards.greenhouse.io/anthropic?error=true", True),
    ("https://boards.greenhouse.io/anthropic?error=true", True),
    ("https://boards.greenhouse.io/acme/jobs/1", False),
    ("https://example.com/jobs/1?error=true", False),
])
def test_is_error_redirect(url, expected):
    assert greenhouse.is_error_redirect(url) == expected
