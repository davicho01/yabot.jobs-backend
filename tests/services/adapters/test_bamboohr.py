import httpx
import pytest

from app.models.enums import EmploymentType, WorkplaceType
from app.services.adapters import bamboohr, base

JOB_URL = "https://biolite.bamboohr.com/careers/110"
DETAIL_API = "https://biolite.bamboohr.com/careers/110/detail"
COMPANY_API = "https://biolite.bamboohr.com/careers/company-info"

# Trimmed from the live response for this job (2026-10-13).
JOB = {
    "jobOpeningName": "Embedded Software Engineer (IoT)",
    "jobOpeningStatus": "Open",
    "employmentStatusLabel": "Full-Time",
    "location": {"city": "Midvale", "state": "Utah", "postalCode": "84047", "addressCountry": "United States"},
    "description": "<p>Location: Midvale, UT</p><p><strong>Our Benefits:</strong></p><ul><li>Competitive pay</li></ul>",
    "compensation": "$120,000-$135,000",
    "datePosted": "2026-10-13",
    "locationType": "0",
}


def _response(status, url, *, json=None):
    return httpx.Response(status, json=json, request=httpx.Request("GET", url))


def _detail(job):
    return _response(200, DETAIL_API, json={"result": {"jobOpening": job, "formFields": {}}})


@pytest.fixture
def bamboohr_api(monkeypatch):
    """Maps each URL to a response, or to a list served in order; "_calls"
    records every request."""
    calls = []
    api = {
        DETAIL_API: _detail(JOB),
        COMPANY_API: _response(200, COMPANY_API, json={"result": {"name": "BioLite"}}),
        "_calls": calls,
    }

    def fake_get(url, **kwargs):
        calls.append(url)
        served = api[url]
        return served.pop(0) if isinstance(served, list) else served

    monkeypatch.setattr(base.httpx, "get", fake_get)
    monkeypatch.setattr(base.time, "sleep", lambda seconds: None)
    return api


def test_job_is_read_from_the_detail_api(bamboohr_api):
    result = bamboohr.scan_job_url(JOB_URL)

    assert result.success
    assert result.title == "Embedded Software Engineer (IoT)"
    assert result.company_name == "BioLite"
    assert result.location == "Midvale, Utah"
    assert result.workplace_type == WorkplaceType.ONSITE
    assert result.employment_type == EmploymentType.FULL_TIME
    assert (result.salary_min, result.salary_max, result.salary_currency) == (120000, 135000, "USD")
    assert str(result.posted_at) == "2026-10-13"
    assert "Our Benefits:" in result.description and "Competitive pay" in result.description
    assert JOB_URL not in bamboohr_api["_calls"]  # the page itself only has the teaser


@pytest.mark.parametrize(
    "location_type, workplace",
    [("1", WorkplaceType.REMOTE), ("2", WorkplaceType.HYBRID), (None, WorkplaceType.UNKNOWN)],
)
def test_location_type(bamboohr_api, location_type, workplace):
    bamboohr_api[DETAIL_API] = _detail({**JOB, "locationType": location_type})

    assert bamboohr.scan_job_url(JOB_URL).workplace_type == workplace


def test_salary_falls_back_to_the_description(bamboohr_api):
    pay = "<p>Pay: $50,000 - $60,000 a year</p>"
    bamboohr_api[DETAIL_API] = _detail({**JOB, "compensation": None, "description": pay})

    result = bamboohr.scan_job_url(JOB_URL)

    assert (result.salary_min, result.salary_max) == (50000, 60000)


def test_removed_job_is_expired(bamboohr_api):
    bamboohr_api[DETAIL_API] = _response(404, DETAIL_API, json={"type": "not_found"})

    result = bamboohr.scan_job_url(JOB_URL)

    assert (result.success, result.expired) == (False, True)


def test_job_no_longer_open_is_expired(bamboohr_api):
    bamboohr_api[DETAIL_API] = _detail({**JOB, "jobOpeningStatus": "Filled"})

    result = bamboohr.scan_job_url(JOB_URL)

    assert (result.success, result.expired) == (False, True)


def test_server_error_fails_without_expiring(bamboohr_api):
    bamboohr_api[DETAIL_API] = _response(503, DETAIL_API, json={})

    result = bamboohr.scan_job_url(JOB_URL)

    assert (result.success, result.expired) == (False, False)


def test_company_lookup_failure_keeps_the_scan(bamboohr_api):
    bamboohr_api[COMPANY_API] = _response(500, COMPANY_API, json={})

    result = bamboohr.scan_job_url(JOB_URL)

    assert result.success and result.company_name is None


def test_other_urls_are_not_claimed():
    assert bamboohr.scan_job_url("https://biolite.bamboohr.com/careers") is None
    assert bamboohr.scan_job_url("https://example.com/careers/110") is None
