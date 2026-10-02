from app.services.adapters import peopleadmin
from tests.conftest import FakeResponse


def _page(ids):
    return FakeResponse(text="".join(f'<a href="/postings/{i}">x</a>' for i in ids))


def _serve(monkeypatch, pages):
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params["page"])
        return _page(pages.get(params["page"], []))

    monkeypatch.setattr(peopleadmin, "get_with_retry", fake_get)
    return calls


def test_paginates_tenants_with_page_size_below_sixty(monkeypatch):
    # utah.peopleadmin.com serves 30 per page; a fixed 60 cutoff stopped
    # after page 1 and crawled 30 of ~800 postings.
    pages = {1: range(0, 30), 2: range(30, 60), 3: range(60, 72)}
    calls = _serve(monkeypatch, pages)
    urls = peopleadmin._fetch_jobs("utah.peopleadmin.com")
    assert len(urls) == 72
    assert urls[0] == "https://utah.peopleadmin.com/postings/0"
    assert calls == [1, 2, 3]


def test_stops_when_server_ignores_page_param(monkeypatch):
    calls = _serve(monkeypatch, {p: range(0, 30) for p in range(1, 50)})
    assert len(peopleadmin._fetch_jobs("x.peopleadmin.com")) == 30
    assert calls == [1, 2]


def test_full_last_page_ends_on_empty_page(monkeypatch):
    calls = _serve(monkeypatch, {1: range(0, 60), 2: range(60, 120)})
    assert len(peopleadmin._fetch_jobs("x.peopleadmin.com")) == 120
    assert calls == [1, 2, 3]
