from app.models.enums import EmploymentType
from app.services.adapters import workforce_ready
from tests.conftest import FakeResponse

_BOARD = "secure7.saashr.com/6211828"


def test_match_reads_host_and_company_from_job_and_search_urls():
    for url in [
        "https://secure7.saashr.com/ta/6211828.careers?ShowJob=638030883",
        "https://SECURE7.saashr.com/ta/6211828.careers?CareersSearch",
    ]:
        assert workforce_ready._match(url) == _BOARD


def test_fetch_jobs_paginates_until_total(monkeypatch):
    pages = {0: [1, 2], 2: [3]}
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params["offset"])
        jobs = [{"id": i} for i in pages.get(params["offset"], [])]
        return FakeResponse(json_data={"job_requisitions": jobs, "_paging": {"total": 3}})

    monkeypatch.setattr(workforce_ready, "get_with_retry", fake_get)
    urls = workforce_ready._fetch_jobs(_BOARD)
    assert urls[-1] == "https://secure7.saashr.com/ta/6211828.careers?ShowJob=3"
    assert len(urls) == 3 and calls == [0, 2]


def test_scan_builds_result_from_detail_api(monkeypatch):
    job = {
        "job_title": "Welding Instructor",
        "location": {"city": "Roosevelt", "state": "UT", "country": "USA"},
        "base_pay_from": 46969.0,
        "base_pay_to": 70452.0,
        "employee_type": {"name": "FT Exempt"},
        "job_description": "<p>Teach welding.</p>",
        "job_requirement": "<p>Certified welder.</p>",
    }
    monkeypatch.setattr(workforce_ready, "get_with_retry", lambda url, params, timeout: FakeResponse(json_data=job))
    result = workforce_ready.scan_job_url("https://secure7.saashr.com/ta/6211828.careers?ShowJob=638032146")
    assert result.success and result.title == "Welding Instructor"
    assert result.location == "Roosevelt, UT, USA"
    assert result.employment_type == EmploymentType.FULL_TIME
    assert (result.salary_min, result.salary_max, result.salary_currency) == (46969, 70452, "USD")
    assert "Teach welding." in result.description and "Certified welder." in result.description


def test_scan_flags_invalid_requisition_as_expired(monkeypatch):
    body = {"errors": [{"code": 40197, "message": "Job Requisition Id is not valid"}]}
    monkeypatch.setattr(
        workforce_ready, "get_with_retry", lambda url, params, timeout: FakeResponse(json_data=body, status_code=400)
    )
    result = workforce_ready.scan_job_url("https://secure7.saashr.com/ta/6211828.careers?ShowJob=1")
    assert not result.success and result.expired


def test_scan_ignores_search_page():
    assert workforce_ready.scan_job_url("https://secure7.saashr.com/ta/6211828.careers?CareersSearch") is None
