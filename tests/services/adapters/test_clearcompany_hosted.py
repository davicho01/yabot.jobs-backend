from datetime import date

import httpx
import pytest

from app.models.enums import WorkplaceType
from app.services.adapters import clearcompany
from tests.conftest import FakeResponse

SITE_ID = "8c925009-ffc1-ef7e-aca6-5e6849a8ab7a"
JOB_A = "f71c1213-fe6a-9202-9704-206536d822c5"
JOB_B = "88c0e018-3566-957e-1afb-1c5d3067ab33"
SHELL = f'<body><script src="https://careers-content.clearcompany.com/js/v1/career-site.js" data-site-id="{SITE_ID}"></script></body>'


def _job(job_id, **extra):
    return {
        "id": job_id,
        "positionTitle": "Engineer",
        "applyLink": f"https://acme.clearcompany.com/careers/jobs/{job_id}/apply",
        "postedDate": "2026-10-26T00:00:00",
        **extra,
    }


def _network(monkeypatch, routes):
    """routes: url prefix -> FakeResponse or callable(params) -> FakeResponse."""
    calls = []

    def fake_get(url, params=None, **kwargs):
        calls.append((url, params))
        for prefix, response in routes.items():
            if url.startswith(prefix):
                return response(params) if callable(response) else response
        raise AssertionError(f"unexpected request: {url}")

    monkeypatch.setattr(clearcompany, "get_with_retry", fake_get)
    monkeypatch.setattr(clearcompany, "_decode", lambda response: response.text)
    return calls


def test_a_migrated_tenant_is_listed_through_the_api_not_the_hosted_pages(monkeypatch):
    calls = _network(
        monkeypatch,
        {
            "https://acme.hrmdirect.com": FakeResponse(text=SHELL),
            f"https://careers-api.clearcompany.com/v1/{SITE_ID}": FakeResponse(
                json_data={"results": [_job(JOB_A), _job(JOB_B)], "totalCount": 2, "currentPageCount": 2}
            ),
        },
    )

    listing = clearcompany._fetch_jobs("acme")

    assert listing.urls == [f"https://acme.clearcompany.com/careers/jobs/{JOB_A}", f"https://acme.clearcompany.com/careers/jobs/{JOB_B}"]
    assert listing.complete
    assert listing.posted[listing.urls[0]] == date(2026, 10, 26)
    assert not any("acme.clearcompany.com" in url for url, _ in calls)  # that host disallows crawling


def test_the_api_is_paged_until_the_total_is_reached(monkeypatch):
    pages = {0: [_job(JOB_A)], 1: [_job(JOB_B)]}
    _network(
        monkeypatch,
        {
            "https://acme.hrmdirect.com": FakeResponse(text=SHELL),
            "https://careers-api.clearcompany.com/v1/": lambda params: FakeResponse(
                json_data={"results": pages.get(params["pageIndex"], []), "totalCount": 2}
            ),
        },
    )

    assert len(clearcompany._fetch_jobs("acme").urls) == 2


def test_a_migrated_tenant_with_no_jobs_is_empty_and_a_bad_api_answer_is_an_error(monkeypatch):
    api = f"https://careers-api.clearcompany.com/v1/{SITE_ID}"
    _network(monkeypatch, {"https://acme.hrmdirect.com": FakeResponse(text=SHELL), api: FakeResponse(json_data={"results": [], "totalCount": 0})})
    assert clearcompany._fetch_jobs("acme").urls == []

    _network(monkeypatch, {"https://acme.hrmdirect.com": FakeResponse(text=SHELL), api: FakeResponse(json_data={"error": "nope"})})
    with pytest.raises(ValueError):
        clearcompany._fetch_jobs("acme")


def test_a_tenant_still_on_the_legacy_pages_is_listed_as_before(monkeypatch):
    html = '<a href="job-opening.php?req=11">x</a><a href="job-opening.php?req=12">y</a>'
    _network(monkeypatch, {"https://acme.hrmdirect.com": FakeResponse(text=html)})

    assert clearcompany._fetch_jobs("acme") == [
        "https://acme.hrmdirect.com/employment/job-opening.php?req=11#job",
        "https://acme.hrmdirect.com/employment/job-opening.php?req=12#job",
    ]


HOSTED_URL = f"https://acme.clearcompany.com/careers/jobs/{JOB_A}"


def test_a_hosted_job_is_scanned_through_the_api(monkeypatch):
    calls = _network(
        monkeypatch,
        {
            "https://apply.clearcompany.com/acme": FakeResponse(text=SHELL),
            f"https://careers-api.clearcompany.com/v1/{SITE_ID}/{JOB_A}": FakeResponse(
                json_data=_job(
                    JOB_A,
                    positionTitle="Senior Engineer",
                    description="<p>Build things.</p><ul><li>Python</li></ul>",
                    location="Remote (US)",
                    brandName="Acme",
                    salaryRangeLow="120000",
                    salaryRangeHigh="150000",
                )
            ),
        },
    )

    result = clearcompany.scan_job_url(HOSTED_URL + "/apply")

    assert result.success
    assert (result.title, result.company_name, result.location) == ("Senior Engineer", "Acme", "Remote (US)")
    assert result.workplace_type == WorkplaceType.REMOTE
    assert (result.salary_min, result.salary_max) == (120000, 150000)
    assert result.posted_at == date(2026, 10, 26)
    assert "Build things" in result.description
    assert not any(url.startswith("https://acme.clearcompany.com") for url, _ in calls)


def test_a_hosted_job_the_api_no_longer_has_is_expired(monkeypatch):
    gone = httpx.HTTPStatusError("404", request=httpx.Request("GET", "https://x"), response=httpx.Response(404))

    def missing(params):
        raise gone

    _network(
        monkeypatch,
        {"https://apply.clearcompany.com/acme": FakeResponse(text=SHELL), f"https://careers-api.clearcompany.com/v1/{SITE_ID}/": missing},
    )

    result = clearcompany.scan_job_url(HOSTED_URL)

    assert not result.success and result.expired


def test_a_career_site_without_a_site_id_fails_the_scan_without_expiring_it(monkeypatch):
    _network(monkeypatch, {"https://apply.clearcompany.com/acme": FakeResponse(text="<html>moved</html>")})

    result = clearcompany.scan_job_url(HOSTED_URL)

    assert not result.success and not result.expired
