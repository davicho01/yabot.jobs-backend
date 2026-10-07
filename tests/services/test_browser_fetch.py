import httpx

from app.core.config import settings
from app.services import browser_fetch

URL = "https://jobs.dominos.com/us/jobs/some-uuid/some-slug/"


class _FakeHttpResponse:
    def __init__(self, html: str | None):
        self._html = html

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"html": self._html, "final_url": URL}


def test_fetch_rendered_page_retries_after_a_failed_attempt(monkeypatch):
    monkeypatch.setattr(settings, "browser_fetch_service_url", "https://browser.example")
    calls = []

    def fake_post(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.HTTPError("challenge not cleared")
        return _FakeHttpResponse(html="<html>real content</html>")

    monkeypatch.setattr(browser_fetch.httpx, "post", fake_post)

    page = browser_fetch.fetch_rendered_page(URL)

    assert page is not None
    assert page.html == "<html>real content</html>"
    assert len(calls) == 2


def test_fetch_rendered_page_returns_none_after_every_attempt_fails(monkeypatch):
    monkeypatch.setattr(settings, "browser_fetch_service_url", "https://browser.example")
    calls = []

    def fake_post(*_args, **_kwargs):
        calls.append(1)
        raise httpx.HTTPError("challenge not cleared")

    monkeypatch.setattr(browser_fetch.httpx, "post", fake_post)

    page = browser_fetch.fetch_rendered_page(URL)

    assert page is None
    assert len(calls) == browser_fetch._RENDER_ATTEMPTS


def test_fetch_rendered_page_succeeds_on_first_attempt_without_retrying(monkeypatch):
    monkeypatch.setattr(settings, "browser_fetch_service_url", "https://browser.example")
    calls = []

    def fake_post(*_args, **_kwargs):
        calls.append(1)
        return _FakeHttpResponse(html="<html>real content</html>")

    monkeypatch.setattr(browser_fetch.httpx, "post", fake_post)

    page = browser_fetch.fetch_rendered_page(URL)

    assert page is not None
    assert len(calls) == 1


CHALLENGE = "<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>"


def test_a_bot_challenge_render_counts_as_a_failed_attempt(monkeypatch):
    monkeypatch.setattr(settings, "browser_fetch_service_url", "https://browser.example")
    monkeypatch.setattr(browser_fetch.httpx, "post", lambda *_a, **_kw: _FakeHttpResponse(html=CHALLENGE))

    assert browser_fetch.fetch_rendered_page(URL) is None


def test_is_bot_challenge_page_matches_the_whole_title_only():
    assert browser_fetch.is_bot_challenge_page(CHALLENGE)
    assert browser_fetch.is_bot_challenge_page("<title> Access  Denied </title>")
    assert browser_fetch.is_bot_challenge_page("<title>Attention Required! &#124; Cloudflare</title>")
    assert not browser_fetch.is_bot_challenge_page("<title>Just a moment of your time: Sales Associate</title>")
    assert not browser_fetch.is_bot_challenge_page("<title>Access Denied Investigator</title>")
    assert not browser_fetch.is_bot_challenge_page("<html>no title</html>")
