import httpx
import pytest

from app.models.enums import EmploymentType, WorkplaceType
from app.services.adapters import base, workable

SHORTLINK = "https://apply.workable.com/j/F853A573AA"
ACCOUNT_URL = "https://apply.workable.com/manilarecruitment/j/F853A573AA"
JOB_API = "https://apply.workable.com/api/v2/accounts/manilarecruitment/jobs/F853A573AA"
ACCOUNT_API = "https://apply.workable.com/api/v1/accounts/manilarecruitment"

JOB = {
    "title": "Finance, Accounting & Tax Manager - #35342",
    "description": "<p>Our client distributes Indofood products.</p>",
    "requirements": "<ul><li>CPA license</li></ul>",
    "benefits": "",
    "type": "full",
    "workplace": "on_site",
    "remote": False,
    "location": {"city": "Taguig", "region": "Metro Manila", "country": "Philippines"},
    "published": "2026-10-12T00:00:00.000Z",
    "state": "published",
}


def _response(status, url, *, json=None, headers=None):
    return httpx.Response(status, json=json, headers=headers, request=httpx.Request("GET", url))


def _redirect(location):
    return _response(301, SHORTLINK, headers={"location": location})


@pytest.fixture
def workable_api(monkeypatch):
    """Fakes Workable underneath the real retry helper: maps each URL to a
    response, or to a list of responses served in order (e.g. a 429 then a
    200). Returns that dict to tweak; "_calls" records every request."""
    workable._account_cache.clear()
    calls = []
    api = {
        SHORTLINK: _redirect("/manilarecruitment/j/F853A573AA"),
        JOB_API: _response(200, JOB_API, json=JOB),
        ACCOUNT_API: _response(200, ACCOUNT_API, json={"name": "Manila Recruitment", "url": "http://manilarecruitment.com"}),
        "_calls": calls,
    }

    def fake_get(url, **kwargs):
        calls.append(url)
        served = api[url]
        return served.pop(0) if isinstance(served, list) else served

    monkeypatch.setattr(base.httpx, "get", fake_get)
    monkeypatch.setattr(base.time, "sleep", lambda seconds: None)
    return api


def test_shortlink_is_resolved_and_read_from_the_job_api(workable_api):
    result = workable.scan_job_url(SHORTLINK)

    assert result.success
    assert result.title == "Finance, Accounting & Tax Manager - #35342"
    assert result.company_name == "Manila Recruitment"
    assert result.company_url == "http://manilarecruitment.com"
    assert result.location == "Taguig, Metro Manila, Philippines"
    assert result.workplace_type == WorkplaceType.ONSITE
    assert result.employment_type == EmploymentType.FULL_TIME
    assert str(result.posted_at) == "2026-10-12"
    assert "Indofood" in result.description and "CPA license" in result.description
    assert "Requirements" in result.description and "Benefits" not in result.description


def test_account_url_needs_no_redirect(workable_api):
    result = workable.scan_job_url(ACCOUNT_URL)

    assert result.success
    assert SHORTLINK not in workable_api["_calls"]


def test_removed_job_shortlink_is_expired(workable_api):
    workable_api[SHORTLINK] = _redirect("/oops")

    result = workable.scan_job_url(SHORTLINK)

    assert (result.success, result.expired) == (False, True)


def test_job_api_404_is_expired(workable_api):
    workable_api[JOB_API] = _response(404, JOB_API, json={})

    result = workable.scan_job_url(ACCOUNT_URL)

    assert (result.success, result.expired) == (False, True)


def test_unpublished_job_is_expired(workable_api):
    workable_api[JOB_API] = _response(200, JOB_API, json={**JOB, "state": "archived"})

    result = workable.scan_job_url(ACCOUNT_URL)

    assert (result.success, result.expired) == (False, True)


def test_server_error_fails_without_expiring(workable_api):
    workable_api[JOB_API] = _response(503, JOB_API, json={})

    result = workable.scan_job_url(ACCOUNT_URL)

    assert (result.success, result.expired) == (False, False)


def test_account_lookup_failure_keeps_the_scan(workable_api):
    workable_api[ACCOUNT_API] = _response(500, ACCOUNT_API, json={})

    result = workable.scan_job_url(ACCOUNT_URL)

    assert result.success and result.company_name is None


def test_rate_limited_job_is_retried_once(workable_api):
    workable_api[JOB_API] = [_response(429, JOB_API), _response(200, JOB_API, json=JOB)]

    result = workable.scan_job_url(ACCOUNT_URL)

    assert result.success
    assert workable_api["_calls"].count(JOB_API) == 2


def test_still_rate_limited_after_one_retry_fails_without_expiring(workable_api):
    workable_api[JOB_API] = [_response(429, JOB_API), _response(429, JOB_API), _response(200, JOB_API, json=JOB)]

    result = workable.scan_job_url(ACCOUNT_URL)

    assert (result.success, result.expired) == (False, False)
    assert "429" in result.error
    assert workable_api["_calls"].count(JOB_API) == 2


def test_rate_limited_shortlink_fails_without_expiring(workable_api):
    workable_api[SHORTLINK] = [_response(429, SHORTLINK), _response(429, SHORTLINK)]

    result = workable.scan_job_url(SHORTLINK)

    assert (result.success, result.expired) == (False, False)
    assert workable_api["_calls"].count(SHORTLINK) == 2


def test_failed_account_lookup_is_not_cached(workable_api):
    workable_api[ACCOUNT_API] = [
        _response(429, ACCOUNT_API),
        _response(429, ACCOUNT_API),
        _response(200, ACCOUNT_API, json={"name": "Manila Recruitment"}),
    ]

    assert workable.scan_job_url(ACCOUNT_URL).company_name is None
    assert workable.scan_job_url(ACCOUNT_URL).company_name == "Manila Recruitment"


def test_remote_flag_is_used_when_workplace_is_missing(workable_api):
    workable_api[JOB_API] = _response(200, JOB_API, json={**JOB, "workplace": None, "remote": True})

    assert workable.scan_job_url(ACCOUNT_URL).workplace_type == WorkplaceType.REMOTE


def test_other_hosts_and_board_pages_are_not_claimed():
    assert workable.scan_job_url("https://boards.greenhouse.io/acme/jobs/1") is None
    assert workable.scan_job_url("https://apply.workable.com/manilarecruitment/") is None
