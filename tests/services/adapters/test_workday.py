import pytest

from app.services.adapters import workday
from tests.conftest import FakeResponse


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Posted Today", 0),
        ("Posted Yesterday", 1),
        ("Posted 7 Days Ago", 7),
        ("Posted 30+ Days Ago", 30),
        (None, None),
        ("", None),
        ("garbage", None),
    ],
)
def test_posted_age_days(label, expected):
    assert workday._posted_age_days(label) == expected


def test_match_extracts_company_instance_site():
    url = "https://archwellessentials.wd1.myworkdayjobs.com/Careers/job/Remote-USA/Some-Title_JR107216"
    assert workday._match(url) == "archwellessentials/wd1/Careers"


def test_match_returns_none_for_unrelated_url():
    assert workday._match("https://careers.freedommortgage.com/us/en/job/JR107216/Some-Title") is None


def test_detect_embedded_finds_workday_link_in_page_html(monkeypatch):
    # The Freedom Mortgage / Phenom case: the submitted URL is the
    # marketing site's own domain, but the page's JSON blob links out to a
    # real myworkdayjobs.com apply URL.
    page_html = (
        '{"applyUrl":"https://archwellessentials.wd1.myworkdayjobs.com/Careers/job/'
        'Remote-USA/Full-Stack-AI-Software-Engineer_JR107216/apply","other":"field"}'
    )
    monkeypatch.setattr(workday.httpx, "get", lambda *a, **k: FakeResponse(text=page_html))
    result = workday._detect_embedded("https://careers.freedommortgage.com/us/en/job/JR107216/Full-Stack-AI-Software-Engineer")
    assert result == "archwellessentials/wd1/Careers"


def test_detect_embedded_returns_none_when_no_workday_link(monkeypatch):
    monkeypatch.setattr(workday.httpx, "get", lambda *a, **k: FakeResponse(text="<html>nothing here</html>"))
    assert workday._detect_embedded("https://careers.example.com/job/123") is None


def test_detect_embedded_returns_none_on_fetch_failure(monkeypatch):
    def raise_error(*a, **k):
        raise workday.httpx.ConnectError("boom")

    monkeypatch.setattr(workday.httpx, "get", raise_error)
    assert workday._detect_embedded("https://careers.example.com/job/123") is None


def _posting(external_path: str, posted_on: str) -> dict:
    return {"externalPath": external_path, "postedOn": posted_on}


def test_fetch_jobs_stops_at_recent_window_boundary(monkeypatch):
    # One page: a mix of within-window and outside-window postings (mirrors
    # the interleaving seen against a real large Workday tenant) — only the
    # within-window ones should come back, and pagination should stop
    # rather than continuing past the boundary.
    page_1 = [
        _posting("/job/a", "Posted Today"),
        _posting("/job/b", f"Posted {workday.RECENT_WINDOW_DAYS - 1} Days Ago"),
        _posting("/job/c", f"Posted {workday.RECENT_WINDOW_DAYS} Days Ago"),  # exactly at the boundary, excluded
        _posting("/job/d", "Posted 30+ Days Ago"),
    ]
    calls = []

    def fake_post(url, json, **kwargs):
        calls.append(json["offset"])
        return FakeResponse(json_data={"jobPostings": page_1})

    monkeypatch.setattr(workday, "post_with_retry", fake_post)
    urls = workday._fetch_jobs("acme/wd1/Careers")
    assert urls == [
        "https://acme.wd1.myworkdayjobs.com/Careers/job/a",
        "https://acme.wd1.myworkdayjobs.com/Careers/job/b",
    ]
    assert calls == [0]  # stopped after the first page, no further pagination


def test_fetch_jobs_paginates_while_fully_within_window(monkeypatch):
    pages = {
        0: [_posting(f"/job/{i}", "Posted Today") for i in range(workday._WORKDAY_PAGE_SIZE)],
        workday._WORKDAY_PAGE_SIZE: [_posting("/job/last", "Posted Yesterday")],
    }

    def fake_post(url, json, **kwargs):
        return FakeResponse(json_data={"jobPostings": pages.get(json["offset"], [])})

    monkeypatch.setattr(workday, "post_with_retry", fake_post)
    urls = workday._fetch_jobs("acme/wd1/Careers")
    assert len(urls) == workday._WORKDAY_PAGE_SIZE + 1


def test_fetch_jobs_uses_four_day_window(monkeypatch):
    monkeypatch.setattr(workday, "RECENT_WINDOW_DAYS", 4)
    page = [_posting(f"/job/{day}", f"Posted {day} Days Ago") for day in range(1, 8)]
    monkeypatch.setattr(workday, "post_with_retry", lambda *a, **k: FakeResponse(json_data={"jobPostings": page}))
    urls = workday._fetch_jobs("acme/wd1/Careers")
    assert urls == [f"https://acme.wd1.myworkdayjobs.com/Careers/job/{day}" for day in (1, 2, 3)]


def test_job_url_re_matches_bare_url():
    match = workday._JOB_URL_RE.search(
        "https://stryker.wd1.myworkdayjobs.com/StrykerCareers/job/Kalamazoo-Michigan/Senior-Engineer_R570975-1"
    )
    assert match is not None
    assert match.groups() == (
        "stryker",
        "wd1",
        "StrykerCareers",
        "/job/Kalamazoo-Michigan/Senior-Engineer_R570975-1",
    )


def test_job_url_re_stops_at_href_quote_when_embedded_in_html():
    # The careers.stryker.com bug: branded career sites embed the real
    # myworkdayjobs.com URL inside an href attribute rather than exposing it
    # as the address-bar URL. Before the job_path group excluded quotes, it
    # ran straight through the closing '"' into the rest of the page,
    # producing a multi-line "job_path" that crashed httpx.get with
    # InvalidURL instead of a clean per-job API path.
    html = (
        '<a href="https://stryker.wd1.myworkdayjobs.com/StrykerCareers/job/Kalamazoo-Michigan/'
        'Senior-Engineer_R570975-1" target="_blank" class="font-bold">Apply</a>\n'
        '<a href="https://stryker.wd1.myworkdayjobs.com/StrykerCareers/login">Login</a>'
    )
    match = workday._JOB_URL_RE.search(html)
    assert match is not None
    company, instance, site, job_path = match.groups()
    assert (company, instance, site) == ("stryker", "wd1", "StrykerCareers")
    assert job_path == "/job/Kalamazoo-Michigan/Senior-Engineer_R570975-1"
    assert "\n" not in job_path
    assert '"' not in job_path


def test_extract_fetches_by_url_and_returns_rich_description(monkeypatch):
    url = "https://stryker.wd1.myworkdayjobs.com/StrykerCareers/job/Kalamazoo-Michigan/Senior-Engineer_R570975-1"
    calls = []

    def fake_get(fetch_url, timeout):
        calls.append(fetch_url)
        return FakeResponse(json_data={"jobPostingInfo": {"jobDescription": "<p>Build <b>medical devices</b>.</p>"}})

    monkeypatch.setattr(workday.httpx, "get", fake_get)
    result = workday.extract(url, "<html></html>")
    assert result.description == "Build **medical devices**."
    assert calls == [
        "https://stryker.wd1.myworkdayjobs.com/wday/cxs/stryker/StrykerCareers"
        "/job/Kalamazoo-Michigan/Senior-Engineer_R570975-1"
    ]


def test_extract_falls_back_to_url_embedded_in_html(monkeypatch):
    # The careers.stryker.com case: the submitted URL is the branded
    # marketing domain, not myworkdayjobs.com directly, but the real
    # Workday URL is embedded in the page's own HTML (an apply/login link).
    html = (
        '<a href="https://stryker.wd1.myworkdayjobs.com/StrykerCareers/job/Kalamazoo-Michigan/'
        'Senior-Engineer_R570975-1" target="_blank">Apply</a>'
    )
    monkeypatch.setattr(workday.httpx, "get", lambda *a, **k: FakeResponse(
        json_data={"jobPostingInfo": {"jobDescription": "<p>Build things.</p>"}}))
    result = workday.extract("https://careers.stryker.com/job/R570975-1", html)
    assert result.description == "Build things."


def test_extract_none_when_neither_url_nor_html_match():
    assert workday.extract("https://example.com/jobs/1", "<html>unrelated</html>") is None


def test_fetch_jobs_stops_after_500_recent_jobs(monkeypatch):
    calls = []

    def fake_post(url, json, **kwargs):
        offset = json["offset"]
        calls.append(offset)
        return FakeResponse(json_data={"jobPostings": [
            _posting(f"/job/{i}", "Posted Today") for i in range(offset, offset + json["limit"])
        ]})

    monkeypatch.setattr(workday, "post_with_retry", fake_post)
    urls = workday._fetch_jobs("acme/wd1/Careers")
    assert len(urls) == 500
    assert len(set(urls)) == 500
    assert calls == list(range(0, 500, 20))
