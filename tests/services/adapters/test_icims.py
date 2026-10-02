from app.services.adapters import icims
from tests.conftest import FakeResponse


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
