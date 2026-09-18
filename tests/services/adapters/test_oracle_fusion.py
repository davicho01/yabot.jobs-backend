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
