import httpx
import pytest

from app.services.adapters import base
from app.services.browser_fetch import RenderedPage
from tests.conftest import FakeResponse

URL = "https://delta.avature.net/en_US/careers/JobDetail/some-job/123"


def test_fetch_html_falls_back_to_browser_render_on_empty_body(monkeypatch):
    # Some WAFs (verified live: delta.avature.net) answer a plain httpx
    # request with a 2xx status and an empty body instead of an error status
    # - raise_for_status() never catches this, so without an explicit empty-
    # body check fetch_html would return the challenge page as if it were
    # real content.
    monkeypatch.setattr(base, "_fetch_direct", lambda _url: FakeResponse(text="", status_code=202, url=URL))
    monkeypatch.setattr(
        base, "fetch_rendered_page", lambda _url, **_kw: RenderedPage(html="<html>real content</html>", url=URL)
    )

    page = base.fetch_html(URL)

    assert page.text == "<html>real content</html>"


def test_fetch_html_raises_when_empty_body_and_render_fallback_fails(monkeypatch):
    monkeypatch.setattr(base, "_fetch_direct", lambda _url: FakeResponse(text="   ", status_code=200, url=URL))
    monkeypatch.setattr(base, "fetch_rendered_page", lambda _url, **_kw: None)

    with pytest.raises(httpx.HTTPError):
        base.fetch_html(URL)


def test_fetch_html_uses_direct_response_when_body_present(monkeypatch):
    monkeypatch.setattr(base, "_fetch_direct", lambda _url: FakeResponse(text="<html>ok</html>", url=URL))
    monkeypatch.setattr(
        base, "fetch_rendered_page", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("should not be called"))
    )

    page = base.fetch_html(URL)

    assert page.text == "<html>ok</html>"
