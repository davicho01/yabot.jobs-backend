from datetime import date

import pytest

from app.services.adapters import ashby
from tests.conftest import FakeResponse


@pytest.fixture(autouse=True)
def _empty_board_cache():
    ashby._board_cache.clear()
    yield
    ashby._board_cache.clear()


HOSTED_ID = "d0087910-ab06-4ee7-a6c4-47ab9283780d"
HOSTED_URL = f"https://jobs.ashbyhq.com/anrok/{HOSTED_ID}"


def test_hosted_posting_is_read_from_the_posting_api(monkeypatch):
    # The hosted page's JSON-LD isn't always there (live postings came back
    # as a "Jobs" shell from the generic scanner), so hosted postings are
    # read from the same API as embeds.
    board = {"jobs": [{
        "id": HOSTED_ID, "title": "Software Engineer", "descriptionHtml": "<p>" + "Build tax software. " * 20 + "</p>",
        "location": "San Francisco", "workplaceType": "Hybrid", "employmentType": "FullTime",
        "publishedAt": "2026-09-14T00:00:00Z",
    }]}
    requested = []

    def fake_get(request_url, timeout=None, **kwargs):
        requested.append(request_url)
        if "api.ashbyhq.com" in request_url:
            return FakeResponse(json_data=board)
        return FakeResponse(text="<html></html>", url=request_url)

    monkeypatch.setattr(ashby.base.httpx, "get", fake_get)
    result = ashby.scan_job_url(HOSTED_URL)

    assert result.success and result.title == "Software Engineer"
    assert "Build tax software." in result.description
    assert (result.location, str(result.posted_at)) == ("San Francisco", "2026-09-14")
    assert requested[0] == "https://api.ashbyhq.com/posting-api/job-board/anrok"


def test_hosted_posting_missing_from_the_board_is_expired(monkeypatch):
    monkeypatch.setattr(ashby.base.httpx, "get", lambda url, **kwargs: FakeResponse(json_data={"jobs": []}))

    result = ashby.scan_job_url(HOSTED_URL)

    assert (result.success, result.expired) == (False, True)


def test_board_listing_is_reused_across_jobs(monkeypatch):
    calls = []

    def fake_get(request_url, timeout=None, **kwargs):
        calls.append(request_url)
        return FakeResponse(json_data={"jobs": []})

    monkeypatch.setattr(ashby.base.httpx, "get", fake_get)
    ashby.scan_job_url(HOSTED_URL)
    ashby.scan_job_url(f"https://jobs.ashbyhq.com/anrok/{'0' * 8}-0000-0000-0000-{'0' * 12}")

    assert calls.count("https://api.ashbyhq.com/posting-api/job-board/anrok") == 1


def test_scan_job_url_returns_none_without_embed_job_id():
    assert ashby.scan_job_url("https://www.nexusblack.com/careers") is None


def test_scan_job_url_embedded_widget_returns_structured_fields(monkeypatch):
    # White-label Ashby embed (e.g. nexusblack.com/careers?ashby_jid=...):
    # no ashbyhq string anywhere in the static HTML, board slug only
    # derivable by guessing from the domain and verifying against the
    # known job id.
    job_id = "af403d92-fc21-42d9-bc1f-783b479b46e9"
    url = f"https://www.nexusblack.com/careers?ashby_jid={job_id}"
    board_json = {
        "jobs": [
            {
                "id": job_id,
                "title": "AI Product Specialist (US)",
                "location": "United States",
                "workplaceType": "Remote",
                "employmentType": "FullTime",
                "publishedAt": "2026-09-10T00:00:00Z",
                "descriptionHtml": "<p>Build things.</p>",
                "descriptionPlain": "Build things.",
            }
        ]
    }
    calls = []

    def fake_get(request_url, timeout=None, **kwargs):
        calls.append(request_url)
        if request_url == "https://api.ashbyhq.com/posting-api/job-board/nexusblack":
            return FakeResponse(json_data=board_json)
        if request_url == url:
            return FakeResponse(text='<meta property="og:site_name" content="Nexus Black" />')
        raise AssertionError(f"unexpected url {request_url}")

    monkeypatch.setattr(ashby.base.httpx, "get", fake_get)
    result = ashby.scan_job_url(url)

    assert result.success is True
    assert result.title == "AI Product Specialist (US)"
    assert result.description == "Build things."
    assert result.location == "United States"
    assert result.workplace_type == "remote"
    assert result.employment_type == "full_time"
    assert result.company_name == "Nexus Black"
    assert result.posted_at == date(2026, 9, 10)
    # Board API hit twice: once to verify the guessed slug during
    # detection, once to actually fetch the job's own fields.
    assert calls.count("https://api.ashbyhq.com/posting-api/job-board/nexusblack") == 2


def test_scan_job_url_returns_error_when_job_removed_between_detection_and_fetch(monkeypatch):
    # _detect_embedded only ever confirms a board slug by finding the job
    # id actually listed on it — so scan_job_url's own re-fetch normally
    # can't miss. This simulates the one way it still could: the posting
    # got pulled from the board in the brief window between that
    # verification call and this function's own fetch.
    job_id = "af403d92-fc21-42d9-bc1f-783b479b46e9"
    url = f"https://www.nexusblack.com/careers?ashby_jid={job_id}"
    listed = {"jobs": [{"id": job_id, "title": "AI Product Specialist (US)"}]}
    removed = {"jobs": []}
    responses = iter([listed, removed])

    def fake_get(request_url, timeout=None, **kwargs):
        assert request_url == "https://api.ashbyhq.com/posting-api/job-board/nexusblack"
        return FakeResponse(json_data=next(responses))

    monkeypatch.setattr(ashby.base.httpx, "get", fake_get)
    result = ashby.scan_job_url(url)

    assert result.success is False
    assert result.error is not None
