import httpx
import pytest

from app.models.enums import EmploymentType, WorkplaceType
from app.services.adapters import workable

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


@pytest.fixture
def workable_api(monkeypatch):
    """Fakes Workable: the shortlink redirect (httpx.get) and the job/account
    APIs (get_with_retry). Returns the dict of API responses to tweak."""
    workable._account_info.cache_clear()
    calls = []
    api = {
        JOB_API: _response(200, JOB_API, json=JOB),
        ACCOUNT_API: _response(200, ACCOUNT_API, json={"name": "Manila Recruitment", "url": "http://manilarecruitment.com"}),
    }
    redirects = {SHORTLINK: "/manilarecruitment/j/F853A573AA"}

    def fake_get(url, **kwargs):
        calls.append(url)
        return _response(301, url, headers={"location": redirects[url]})

    def fake_get_with_retry(url, **kwargs):
        calls.append(url)
        return api[url]

    monkeypatch.setattr(workable.httpx, "get", fake_get)
    monkeypatch.setattr(workable, "get_with_retry", fake_get_with_retry)
    api["_calls"] = calls
    api["_redirects"] = redirects
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
    workable_api["_redirects"][SHORTLINK] = "/oops"

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


def test_remote_flag_is_used_when_workplace_is_missing(workable_api):
    workable_api[JOB_API] = _response(200, JOB_API, json={**JOB, "workplace": None, "remote": True})

    assert workable.scan_job_url(ACCOUNT_URL).workplace_type == WorkplaceType.REMOTE


def test_other_hosts_and_board_pages_are_not_claimed():
    assert workable.scan_job_url("https://boards.greenhouse.io/acme/jobs/1") is None
    assert workable.scan_job_url("https://apply.workable.com/manilarecruitment/") is None
