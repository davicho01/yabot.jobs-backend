import pytest

from app.services.adapters import icims
from tests.conftest import FakeResponse


def _probe(monkeypatch, *, jibe, classic):
    monkeypatch.setattr(icims, "_fetch_jibe_jobs", lambda host: jibe)
    monkeypatch.setattr(icims, "_classic_or_redirect_host", lambda host: classic)


def test_a_board_neither_product_answers_is_an_error_not_an_empty_board(monkeypatch):
    # Every HTTP error on the classic page used to end in [], recorded as a
    # clean zero-job crawl.
    _probe(monkeypatch, jibe=None, classic=(None, None))

    with pytest.raises(ValueError):
        icims._fetch_jobs("x.icims.com")


def test_a_jibe_stub_whose_vanity_api_is_down_is_an_error(monkeypatch):
    _probe(monkeypatch, jibe=None, classic=(None, "careers.x.com"))

    with pytest.raises(ValueError):
        icims._fetch_jobs("x.icims.com")


def test_a_readable_classic_board_with_no_jobs_is_still_empty(monkeypatch):
    _probe(monkeypatch, jibe=None, classic=([], None))

    assert icims._fetch_jobs("x.icims.com") == []


def _serve(monkeypatch, pages):
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params["pr"])
        ids = pages.get(params["pr"], [])
        return FakeResponse(text="".join(f'<a href="https://x.icims.com/jobs/{i}/role/job?in_iframe=1">' for i in ids))

    monkeypatch.setattr(icims, "get_with_retry", fake_get)
    return calls


def test_classic_paginates_tenants_with_small_page_size(monkeypatch):
    # careers-uuhc serves 10 per page; a fixed 50 cutoff stopped after page 0.
    calls = _serve(monkeypatch, {0: range(0, 10), 1: range(10, 20), 2: range(20, 23)})
    assert len(icims._fetch_classic_jobs("x.icims.com")) == 23
    assert calls == [0, 1, 2]


def test_classic_stops_when_server_ignores_page_param(monkeypatch):
    calls = _serve(monkeypatch, {p: range(0, 10) for p in range(50)})
    assert len(icims._fetch_classic_jobs("x.icims.com")) == 10
    assert calls == [0, 1]
