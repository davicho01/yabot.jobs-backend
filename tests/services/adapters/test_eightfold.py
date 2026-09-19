from datetime import date

from app.services.adapters import eightfold
from tests.conftest import FakeResponse


def test_domain_is_valid_rejects_200_with_null_data(monkeypatch):
    # The Freedom Mortgage bug: careers.freedommortgage.com (a Phenom site,
    # not Eightfold) answers /api/pcsx/search with HTTP 200 and a failure
    # envelope ({"data": null, "errorMsg": "Tenant not identified"}).
    # Before the fix, this looked "valid" (raise_for_status never fires on
    # 200), and the caller then crashed trying to call .get() on the null
    # data. Now the body shape itself must show a real tenant.
    monkeypatch.setattr(
        eightfold,
        "get_with_retry",
        lambda *a, **k: FakeResponse(
            json_data={"status": "failure", "errorCode": None, "errorMsg": "Tenant not identified", "data": None}
        ),
    )
    assert eightfold._domain_is_valid("careers.freedommortgage.com", "freedommortgage.com") is False


def test_domain_is_valid_accepts_real_tenant_with_zero_jobs(monkeypatch):
    # A genuinely valid tenant with no open roles right now must not be
    # mistaken for an invalid one — "data" is still a dict, just empty.
    monkeypatch.setattr(
        eightfold,
        "get_with_retry",
        lambda *a, **k: FakeResponse(json_data={"status": "success", "data": {"positions": [], "count": 0}}),
    )
    assert eightfold._domain_is_valid("jobs.example.com", "example.com") is True


def test_domain_is_valid_rejects_http_error(monkeypatch):
    monkeypatch.setattr(eightfold, "get_with_retry", lambda *a, **k: FakeResponse(status_code=403))
    assert eightfold._domain_is_valid("jobs.example.com", "example.com") is False


def test_detect_embedded_no_job_id_does_not_crash_on_false_positive_domain(monkeypatch):
    # End-to-end regression for the actual crash: no signature string on the
    # page, and every candidate domain guess 200s with a null-data body.
    # Before the fix this raised an unhandled AttributeError out of
    # detect_embedded_ats_source; now it must resolve to None instead.
    monkeypatch.setattr(
        eightfold.httpx,
        "get",
        lambda *a, **k: FakeResponse(text="<html>no eightfold signature here</html>"),
    )
    monkeypatch.setattr(
        eightfold,
        "get_with_retry",
        lambda *a, **k: FakeResponse(json_data={"status": "failure", "data": None, "errorMsg": "Tenant not identified"}),
    )
    result = eightfold._detect_embedded("https://careers.freedommortgage.com/us/en/job/JR107216/Some-Title")
    assert result is None


_SIGNATURE_HTML = '<a href="https://eightfold.ai/privacy-policy">Privacy</a>'


def test_extract_fetches_position_details_and_maps_fields(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url, params))
        return FakeResponse(json_data={"data": {
            "id": "555", "name": "Voice Engineer", "location": "Remote - US",
            "jobDescription": "<p>Build <b>voice agents</b>.</p>", "creationTs": 1767225600,
        }})

    monkeypatch.setattr(eightfold.httpx, "get", fake_get)
    result = eightfold.extract("https://jobs.twilio.com/careers/job/555", _SIGNATURE_HTML)
    assert result.title == "Voice Engineer"
    assert result.location == "Remote - US"
    assert result.description == "Build **voice agents**."
    # date.fromtimestamp is local-timezone-dependent (that's the adapter's
    # own behavior, not something to paper over here) — recompute the
    # same way rather than hardcoding a timezone-fragile literal date.
    assert result.posted_at == date.fromtimestamp(1767225600)
    assert calls[0][0] == "https://jobs.twilio.com/api/pcsx/position_details"
    assert calls[0][1] == {"position_id": "555", "domain": "jobs.twilio.com"}


def test_extract_none_without_signature_in_html(monkeypatch):
    def unexpected(*a, **k):
        raise AssertionError("Unexpected API request")
    monkeypatch.setattr(eightfold.httpx, "get", unexpected)
    assert eightfold.extract("https://jobs.twilio.com/careers/job/555", "<html>no signature</html>") is None


def test_extract_none_when_url_has_no_job_id(monkeypatch):
    def unexpected(*a, **k):
        raise AssertionError("Unexpected API request")
    monkeypatch.setattr(eightfold.httpx, "get", unexpected)
    assert eightfold.extract("https://jobs.twilio.com/careers", _SIGNATURE_HTML) is None


def test_extract_none_when_every_candidate_domain_fails(monkeypatch):
    monkeypatch.setattr(eightfold.httpx, "get", lambda *a, **k: FakeResponse(status_code=403))
    assert eightfold.extract("https://jobs.twilio.com/careers/job/555", _SIGNATURE_HTML) is None


def test_candidate_domains_unchanged_for_ordinary_hosts():
    assert eightfold._candidate_domains("jobs.twilio.com") == ["jobs.twilio.com", "twilio.com"]


def test_candidate_domains_hint_goes_first():
    assert eightfold._candidate_domains("jobs.twilio.com", "twilio.com") == ["twilio.com", "jobs.twilio.com"]


def test_candidate_domains_guesses_tenant_dot_com_for_eightfold_hosted_tenants():
    # <tenant>.eightfold.ai says nothing about the tenant's own domain, so
    # with no hint the only useful guess is <tenant>.com — tried after the
    # host and its parent (eightfold.ai, Eightfold's own app tenant).
    assert eightfold._candidate_domains("paypal.eightfold.ai") == ["paypal.eightfold.ai", "eightfold.ai", "paypal.com"]
    assert eightfold._candidate_domains("paypal.eightfold.ai", "paypal.com") == [
        "paypal.com",
        "paypal.eightfold.ai",
        "eightfold.ai",
    ]


def test_domain_hint_reads_query_param():
    assert eightfold._domain_hint("https://paypal.eightfold.ai/careers/job/1-x?domain=paypal.com") == "paypal.com"
    assert eightfold._domain_hint("https://paypal.eightfold.ai/careers/job/1") is None


def _fake_position_details(job_id: str, real_domain: str):
    # Only the tenant's real domain identifies the job, like the live API:
    # every other guess 404s.
    def fake_get_with_retry(url, params, timeout):
        if params.get("domain") == real_domain:
            return FakeResponse(json_data={"data": {"id": job_id}})
        return FakeResponse(status_code=404)

    return fake_get_with_retry


def test_detect_embedded_resolves_eightfold_hosted_tenant_from_domain_query_param(monkeypatch):
    # The PayPal/Eaton/Boston Scientific bug: a live job URL on
    # <tenant>.eightfold.ai only tried [host, eightfold.ai] as the tenant
    # domain, so detection returned None and the submission landed as an
    # empty pending row. The ?domain= param in the URL names the tenant.
    monkeypatch.setattr(eightfold.httpx, "get", lambda *a, **k: FakeResponse(text=_SIGNATURE_HTML))
    monkeypatch.setattr(eightfold, "get_with_retry", _fake_position_details("274922421933", "paypal.com"))
    url = "https://paypal.eightfold.ai/careers/job/274922421933-sr-manager?domain=paypal.com"
    assert eightfold._detect_embedded(url) == "paypal.eightfold.ai/paypal.com"


def test_detect_embedded_resolves_eightfold_hosted_tenant_without_query_param(monkeypatch):
    # A hand-copied job link often has no ?domain= — the <tenant>.com guess
    # covers it.
    monkeypatch.setattr(eightfold.httpx, "get", lambda *a, **k: FakeResponse(text=_SIGNATURE_HTML))
    monkeypatch.setattr(eightfold, "get_with_retry", _fake_position_details("274922421933", "paypal.com"))
    url = "https://paypal.eightfold.ai/careers/job/274922421933"
    assert eightfold._detect_embedded(url) == "paypal.eightfold.ai/paypal.com"


def test_detect_embedded_still_none_when_no_candidate_matches_the_job(monkeypatch):
    monkeypatch.setattr(eightfold.httpx, "get", lambda *a, **k: FakeResponse(text=_SIGNATURE_HTML))
    monkeypatch.setattr(eightfold, "get_with_retry", lambda *a, **k: FakeResponse(status_code=404))
    url = "https://paypal.eightfold.ai/careers/job/1?domain=paypal.com"
    assert eightfold._detect_embedded(url) is None


def test_extract_uses_domain_query_param_for_position_details(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params["domain"])
        if params["domain"] != "paypal.com":
            return FakeResponse(status_code=404)
        return FakeResponse(json_data={"data": {"id": "555", "name": "Engineer"}})

    monkeypatch.setattr(eightfold.httpx, "get", fake_get)
    result = eightfold.extract("https://paypal.eightfold.ai/careers/job/555?domain=paypal.com", _SIGNATURE_HTML)
    assert result.title == "Engineer"
    assert calls == ["paypal.com"]
