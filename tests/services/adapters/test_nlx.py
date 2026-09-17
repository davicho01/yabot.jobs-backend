from app.services.adapters import nlx
from tests.conftest import FakeResponse


def test_slugify_matches_live_url_shapes():
    # Verified live against careers.dispatchhealth.com hrefs: a plain
    # "City, ST" location and the "Virtual, USA" placeholder both collapse
    # any run of non-alphanumeric characters (comma+space) to one hyphen.
    assert nlx._slugify("Upper Saddle River, NJ") == "upper-saddle-river-nj"
    assert nlx._slugify("Virtual, USA") == "virtual-usa"


def test_job_url_builds_expected_shape():
    job = {
        "guid": "D1C3F4BB180D40F1A8E36535C29DB20D",
        "title_slug": "nurse-practitioner-fnp-or-physician-assistant-pa-c",
        "location_exact": "Upper Saddle River, NJ",
    }
    assert nlx._job_url("careers.dispatchhealth.com", job) == (
        "https://careers.dispatchhealth.com/upper-saddle-river-nj/"
        "nurse-practitioner-fnp-or-physician-assistant-pa-c/"
        "D1C3F4BB180D40F1A8E36535C29DB20D/job/"
    )


def test_job_url_none_when_a_required_field_is_missing():
    assert nlx._job_url("careers.dispatchhealth.com", {"guid": "X", "title_slug": "y"}) is None


def test_fetch_jobs_paginates_until_no_more_pages(monkeypatch):
    pages = [
        {
            "jobs": [
                {"guid": "A" * 32, "title_slug": "role-one", "location_exact": "Denver, CO"},
            ],
            "pagination": {"has_more_pages": True},
        },
        {
            "jobs": [
                {"guid": "B" * 32, "title_slug": "role-two", "location_exact": "Virtual, USA"},
            ],
            "pagination": {"has_more_pages": False},
        },
    ]
    calls = []

    def fake_get(url, params, headers, **kwargs):
        calls.append((params["page"], headers["X-Origin"]))
        return FakeResponse(json_data=pages[params["page"] - 1])

    monkeypatch.setattr(nlx, "get_with_retry", fake_get)
    urls = nlx._fetch_jobs("careers.dispatchhealth.com")

    assert urls == [
        f"https://careers.dispatchhealth.com/denver-co/role-one/{'A' * 32}/job/",
        f"https://careers.dispatchhealth.com/virtual-usa/role-two/{'B' * 32}/job/",
    ]
    assert calls == [(1, "careers.dispatchhealth.com"), (2, "careers.dispatchhealth.com")]


def test_fetch_jobs_skips_jobs_missing_url_fields(monkeypatch):
    monkeypatch.setattr(
        nlx,
        "get_with_retry",
        lambda *a, **k: FakeResponse(
            json_data={"jobs": [{"guid": None, "title_slug": None}], "pagination": {"has_more_pages": False}}
        ),
    )
    assert nlx._fetch_jobs("careers.dispatchhealth.com") == []


def test_detect_embedded_matches_on_signature(monkeypatch):
    monkeypatch.setattr(
        nlx.httpx,
        "get",
        lambda *a, **k: FakeResponse(
            text='<link rel="icon" href="https://seo.nlx.org/dispatch-health/img/favicon.ico">'
        ),
    )
    assert nlx._detect_embedded("https://careers.dispatchhealth.com") == "careers.dispatchhealth.com"


def test_detect_embedded_none_without_signature(monkeypatch):
    monkeypatch.setattr(nlx.httpx, "get", lambda *a, **k: FakeResponse(text="<html>unrelated careers page</html>"))
    assert nlx._detect_embedded("https://careers.example.com") is None


def test_detect_embedded_none_on_http_error(monkeypatch):
    monkeypatch.setattr(nlx.httpx, "get", lambda *a, **k: FakeResponse(status_code=500))
    assert nlx._detect_embedded("https://careers.dispatchhealth.com") is None


def test_board_key_is_host():
    assert nlx._board_key("https://careers.dispatchhealth.com/some/path") == "careers.dispatchhealth.com"
    assert nlx._board_key("not-a-url") is None
