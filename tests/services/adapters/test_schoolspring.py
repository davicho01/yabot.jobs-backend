from app.models.enums import EmploymentType
from app.services.adapters import schoolspring
from tests.conftest import FakeResponse

_HOST = "slcschools.schoolspring.com"
_JOB_URL = "https://slcschools.schoolspring.com/jobdetail?jobId=5952581"


def test_match_ignores_national_aggregator_and_api_hosts():
    assert schoolspring._match(_JOB_URL) == _HOST
    assert schoolspring._match("https://www.schoolspring.com/jobs?keyword=teacher") is None
    assert schoolspring._match("https://api.schoolspring.com/api/Jobs/1") is None


def test_fetch_jobs_pages_until_a_short_page(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params["page"])
        count = schoolspring._PAGE_SIZE if params["page"] == 1 else 1
        start = (params["page"] - 1) * schoolspring._PAGE_SIZE
        jobs = [{"jobId": start + i + 1} for i in range(count)]
        assert params["domainName"] == _HOST and params["keyword"] == ""
        return FakeResponse(json_data={"success": True, "value": {"jobsList": jobs}})

    monkeypatch.setattr(schoolspring, "get_with_retry", fake_get)
    urls = schoolspring._fetch_jobs(_HOST)
    assert len(urls) == schoolspring._PAGE_SIZE + 1 and calls == [1, 2]
    assert urls[0] == "https://slcschools.schoolspring.com/jobdetail?jobId=1"


def test_scan_maps_detail_api_fields(monkeypatch):
    detail = {
        "success": True,
        "value": {
            "jobInfo": {
                "jobTitle": "Parapro (SPED) Self Contained (PT)- 440",
                "jobDescription": "<p><strong>Position Details</strong></p><p>Support students.</p>",
                "requirements": None,
                "employerName": "Salt Lake City School District",
                "jobTypeName": "Part-time",
                "payDisplay": 1,
                "payMin": 20.0,
                "payMax": 0.0,
                "displayDate": "2026-10-06T06:00:00",
            },
            "jobLocations": [{"displayLocation": "Salt Lake City, Utah"}, {"displayLocation": "Salt Lake City, Utah"}],
        },
    }
    monkeypatch.setattr(schoolspring, "get_with_retry", lambda url, params, timeout: FakeResponse(json_data=detail))
    result = schoolspring.scan_job_url(_JOB_URL)
    assert result.success and result.title == "Parapro (SPED) Self Contained (PT)- 440"
    assert result.company_name == "Salt Lake City School District"
    assert result.location == "Salt Lake City, Utah"
    assert result.employment_type == EmploymentType.PART_TIME
    assert (result.salary_min, result.salary_max, result.salary_currency) == (20, None, "USD")
    assert result.posted_at.isoformat() == "2026-10-06"
    assert "Support students." in result.description


def test_scan_flags_missing_job_as_expired(monkeypatch):
    body = {"success": False, "message": "JobDetail not found : 1, slcschools.schoolspring.com", "value": {"jobInfo": None}}
    monkeypatch.setattr(schoolspring, "get_with_retry", lambda url, params, timeout: FakeResponse(json_data=body))
    result = schoolspring.scan_job_url("https://slcschools.schoolspring.com/jobdetail?jobId=1")
    assert not result.success and result.expired
