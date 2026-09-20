from datetime import datetime, timedelta, timezone

from app.services.adapters import oracle_fusion
from app.services.adapters.base import RECENT_WINDOW_DAYS
from tests.conftest import FakeResponse


def _req(job_id: str, days_ago: int) -> dict:
    day = datetime.now(timezone.utc).date() - timedelta(days=days_ago)
    return {"Id": job_id, "PostedDate": day.isoformat()}


def test_fetch_jobs_keeps_postings_within_window_and_stops_at_boundary(monkeypatch):
    requisitions = [
        _req("1", 0),
        _req("2", RECENT_WINDOW_DAYS - 1),
        _req("3", RECENT_WINDOW_DAYS),
    ]
    calls = []

    def fake_get(url, params, **kwargs):
        calls.append(params["finder"])
        return FakeResponse(json_data={"items": [{"requisitionList": requisitions}]})

    monkeypatch.setattr(oracle_fusion, "get_with_retry", fake_get)
    urls = oracle_fusion._fetch_jobs("example.oraclecloud.com/CX_1")
    assert urls == [
        oracle_fusion._ORACLE_FUSION_JOB_URL.format(host="example.oraclecloud.com", site_number="CX_1", job_id="1"),
        oracle_fusion._ORACLE_FUSION_JOB_URL.format(host="example.oraclecloud.com", site_number="CX_1", job_id="2"),
    ]
    assert len(calls) == 1  # stopped after the first page


def test_extract_fetches_requisition_details_and_maps_fields(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url, params))
        return FakeResponse(json_data={"items": [{
            "Title": "Solutions Engineer",
            "PrimaryLocation": "Austin, TX",
            "secondaryLocations": [{"Name": "Remote, US"}],
            "JobSchedule": "Full time",
            "WorkplaceTypeCode": "REMOTE",
            "ExternalPostedStartDate": "2026-02-10T00:00:00+00:00",
            "ExternalDescriptionStr": "<p>Own the deal cycle.</p>",
            "ExternalResponsibilitiesStr": "<ul><li>Demo the product</li></ul>",
        }]})

    monkeypatch.setattr(oracle_fusion.httpx, "get", fake_get)
    url = "https://example.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/12345"
    result = oracle_fusion.extract(url, "<html></html>")
    assert result.title == "Solutions Engineer"
    assert result.location == "Austin, TX, Remote, US"
    assert result.workplace_type == "remote"
    assert result.employment_type == "full_time"
    assert result.posted_at.isoformat() == "2026-02-10"
    assert "Own the deal cycle." in result.description
    assert "- Demo the product" in result.description
    assert calls[0][1]["finder"] == 'ById;Id="12345",siteNumber=CX_1'


def test_extract_none_for_non_matching_url(monkeypatch):
    def unexpected(*a, **k):
        raise AssertionError("Unexpected API request")
    monkeypatch.setattr(oracle_fusion.httpx, "get", unexpected)
    assert oracle_fusion.extract("https://example.com/jobs/1", "<html></html>") is None


def test_extract_none_on_http_error(monkeypatch):
    monkeypatch.setattr(oracle_fusion.httpx, "get", lambda *a, **k: FakeResponse(status_code=500))
    url = "https://example.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/12345"
    assert oracle_fusion.extract(url, "<html></html>") is None


def test_workplace_type_of_unset_code_is_unknown():
    assert oracle_fusion._workplace_type_of({}) == "unknown"
    assert oracle_fusion._workplace_type_of({"WorkplaceTypeCode": ""}) == "unknown"


def test_employment_type_of_unmapped_schedule_is_unknown():
    assert oracle_fusion._employment_type_of({"JobSchedule": "Contingent"}) == "unknown"


def test_extract_pulls_salary_and_flex_fields_from_requisition_flex_fields(monkeypatch):
    def fake_get(url, params, timeout):
        return FakeResponse(json_data={"items": [{
            "Title": "Team Leader",
            "PrimaryLocation": "Seattle, WA, United States",
            "Category": "Travel Services",
            "ExternalPostedEndDate": "2026-09-26T04:00:00+00:00",
            "ExternalDescriptionStr": "<p>Lead the lounge team.</p>",
            "requisitionFlexFields": [
                {"Prompt": "Salary Range", "Value": "$65,500 - $81,000 annually + bonus + benefits"},
                {"Prompt": "Career Area", "Value": "Customer Service and Travel"},
            ],
        }]})

    monkeypatch.setattr(oracle_fusion.httpx, "get", fake_get)
    url = "https://example.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/12345"
    result = oracle_fusion.extract(url, "<html></html>")
    assert (result.salary_min, result.salary_max, result.salary_currency) == (65500, 81000, "USD")
    assert result.extracted_fields == {
        "Salary Range": "$65,500 - $81,000 annually + bonus + benefits",
        "Career Area": "Customer Service and Travel",
    }
    assert "Salary Range" in result.description
    assert "Career Area" in result.description
    assert "Job Category" in result.description
    assert "Travel Services" in result.description
    assert "Apply Before" in result.description
    assert "2026-09-26" in result.description


def test_flex_fields_of_ignores_malformed_entries():
    job_data = {"requisitionFlexFields": [
        {"Prompt": "  Career Area  ", "Value": "  Engineering  "},
        {"Prompt": "", "Value": "ignored"},
        {"Prompt": "No Value"},
        "not-a-dict",
    ]}
    assert oracle_fusion._flex_fields_of(job_data) == {"Career Area": "Engineering"}


def test_salary_of_returns_none_without_a_salary_looking_prompt():
    flex_fields = {"Career Area": "Engineering"}
    assert oracle_fusion._salary_of(flex_fields) == (None, None, None)


def test_resolve_vanity_domain_follows_redirect_and_reads_embedded_host(monkeypatch):
    # Amex-shaped: careers.<company>.com/en/sites/{site}/jobs/preview/{id}
    # redirects to its own .../job/{id} page, which embeds the real
    # oraclecloud.com API host in a <base> tag.
    url = "https://careers.example.com/en/sites/CX_1/jobs/preview/26009316"

    def fake_get(requested_url, timeout, follow_redirects):
        assert requested_url == url
        assert follow_redirects is True
        return FakeResponse(text='<html data-apibaseurl="https://egug.fa.us2.oraclecloud.com:443" '
                                  'data-sitenumber="CX_1"></html>')

    monkeypatch.setattr(oracle_fusion.httpx, "get", fake_get)
    assert oracle_fusion._resolve(url) == ("egug.fa.us2.oraclecloud.com", "CX_1", "26009316")


def test_resolve_vanity_domain_served_directly_no_redirect(monkeypatch):
    # TI-shaped: careers.<company>.com/en/sites/{site}/job/{id} serves the
    # SPA shell (with the embedded API host) directly, no redirect involved.
    url = "https://careers.example.com/en/sites/CX/job/25009893"

    monkeypatch.setattr(
        oracle_fusion.httpx,
        "get",
        lambda *a, **k: FakeResponse(
            text='<html data-apibaseurl="https://edbz.fa.us2.oraclecloud.com:443" data-sitenumber="CX"></html>'
        ),
    )
    assert oracle_fusion._resolve(url) == ("edbz.fa.us2.oraclecloud.com", "CX", "25009893")


def test_resolve_vanity_domain_returns_none_without_embedded_host(monkeypatch):
    url = "https://careers.example.com/en/sites/CX_1/job/1"
    monkeypatch.setattr(oracle_fusion.httpx, "get", lambda *a, **k: FakeResponse(text="<html></html>"))
    assert oracle_fusion._resolve(url) is None


def test_resolve_non_oracle_url_skips_network_entirely(monkeypatch):
    def unexpected(*a, **k):
        raise AssertionError("Unexpected network request")

    monkeypatch.setattr(oracle_fusion.httpx, "get", unexpected)
    assert oracle_fusion._resolve("https://example.com/jobs/1") is None


def test_scan_job_url_claims_vanity_domain_job_url(monkeypatch):
    url = "https://careers.example.com/en/sites/CX_1/jobs/preview/26009316"

    monkeypatch.setattr(
        oracle_fusion,
        "_resolve",
        lambda u: ("egug.fa.us2.oraclecloud.com", "CX_1", "26009316"),
    )
    monkeypatch.setattr(
        oracle_fusion,
        "_fetch_job_data",
        lambda u: {
            "Title": "Team Leader",
            "PrimaryLocation": "Seattle, WA, United States",
            "JobSchedule": "Full time",
            "ExternalDescriptionStr": "<p>Lead the lounge team.</p>",
        },
    )
    monkeypatch.setattr(oracle_fusion.base, "fetch_html", lambda u: type("P", (), {"text": "<html></html>"})())

    result = oracle_fusion.scan_job_url(url)
    assert result is not None
    assert result.title == "Team Leader"
    assert result.location == "Seattle, WA, United States"
    assert result.employment_type == "full_time"
    assert "Lead the lounge team." in result.description
