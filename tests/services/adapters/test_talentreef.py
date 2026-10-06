from app.services.adapters import talentreef
from tests.conftest import FakeResponse


def test_client_ids_collects_tenant_and_career_page_clients(monkeypatch):
    pages = [
        {"clientId": 10345, "clients": [{"legacyClientId": "10556"}, {"legacyClientId": "10491"}]},
        {"clientId": 10345, "clients": []},
    ]
    monkeypatch.setattr(talentreef, "get_with_retry", lambda url, timeout: FakeResponse(json_data=pages))
    assert talentreef._client_ids("10345") == ["10345", "10491", "10556"]


def test_client_ids_falls_back_to_tenant_alone(monkeypatch):
    monkeypatch.setattr(talentreef, "get_with_retry", lambda url, timeout: FakeResponse(json_data=[{"clients": []}]))
    assert talentreef._client_ids("10934") == ["10934"]


def test_search_filters_on_brand_and_client(monkeypatch):
    sent = {}

    def fake_post(url, json, timeout):
        sent.update(json)
        return FakeResponse(json_data={"hits": {"total": 0, "hits": []}})

    monkeypatch.setattr(talentreef.httpx, "post", fake_post)
    talentreef._search(["1322", "44"], ["10934"], from_=0)
    filters = sent["query"]["bool"]["filter"]
    # A brand shared across unrelated clients (AFS's "ACE Hardware") must not
    # pull in another client's postings.
    assert {"terms": {"brandId": ["1322", "44"]}} in filters
    assert {"terms": {"clientId": ["10934"]}} in filters
