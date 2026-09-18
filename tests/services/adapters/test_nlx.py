from app.models.enums import EmploymentType, WorkplaceType
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


def test_fetch_job_data_none_when_url_does_not_match_shape():
    assert nlx._fetch_job_data("https://careers.dispatchhealth.com/job-categories/nursing/jobs/") is None


def test_fetch_job_data_fetches_by_host_and_guid(monkeypatch):
    # NLX job pages carry no JSON-LD/og: data of their own — the real
    # record lives in a static per-job JSON keyed by the guid already in
    # the URL, verified live via the browser network tab.
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResponse(json_data={"title": "Senior Platform Engineer"})

    monkeypatch.setattr(nlx.httpx, "get", fake_get)
    guid = "D1C3F4BB180D40F1A8E36535C29DB20D"
    data = nlx._fetch_job_data(f"https://careers.dispatchhealth.com/virtual-usa/senior-platform-engineer/{guid}/job/")
    assert data == {"title": "Senior Platform Engineer"}
    assert calls == [f"https://microsites.dejobs.org/careers-dispatchhealth-com/data/{guid}.json"]


def test_fetch_job_data_none_on_http_error(monkeypatch):
    monkeypatch.setattr(nlx.httpx, "get", lambda *a, **k: FakeResponse(status_code=404))
    guid = "D1C3F4BB180D40F1A8E36535C29DB20D"
    assert nlx._fetch_job_data(f"https://careers.dispatchhealth.com/x/y/{guid}/job/") is None


def test_employment_type_of_maps_known_values():
    assert nlx._employment_type_of({"job_type": "Full Time"}) == EmploymentType.FULL_TIME
    assert nlx._employment_type_of({"job_type": "Part Time"}) == EmploymentType.PART_TIME


def test_employment_type_of_unknown_for_per_diem():
    # "Per Diem" (healthcare as-needed work, verified live) doesn't map
    # cleanly to any EmploymentType value — must fall back to UNKNOWN
    # rather than guessing.
    assert nlx._employment_type_of({"job_type": "Per Diem"}) == EmploymentType.UNKNOWN


def test_workplace_type_of_maps_known_values():
    assert nlx._workplace_type_of({"job_shift": "Remote"}) == WorkplaceType.REMOTE
    assert nlx._workplace_type_of({"job_shift": "On-site"}) == WorkplaceType.ONSITE
    assert nlx._workplace_type_of({"job_shift": "Hybrid"}) == WorkplaceType.HYBRID


def test_posted_at_of_parses_iso_timestamp():
    assert nlx._posted_at_of({"date_added": "2026-09-10T21:52:37.548Z"}).isoformat() == "2026-09-10"


def test_posted_at_of_none_when_missing_or_malformed():
    assert nlx._posted_at_of({}) is None
    assert nlx._posted_at_of({"date_added": "not-a-date"}) is None


def test_description_of_converts_html():
    assert nlx._description_of({"html_description": "<p>Hello <b>world</b></p>"}) == "Hello **world**"


def test_description_of_none_when_blank():
    assert nlx._description_of({"html_description": "  "}) is None
    assert nlx._description_of({}) is None


def test_extract_maps_every_field(monkeypatch):
    monkeypatch.setattr(nlx.httpx, "get", lambda *a, **k: FakeResponse(json_data={
        "title": "Nurse Practitioner",
        "company": "DispatchHealth",
        "location": "Denver, CO",
        "job_type": "Full Time",
        "job_shift": "Remote",
        "date_added": "2026-01-05T00:00:00Z",
        "html_description": "<p>Care for patients <b>virtually</b>.</p>",
    }))
    guid = "D1C3F4BB180D40F1A8E36535C29DB20D"
    url = f"https://careers.dispatchhealth.com/denver-co/nurse-practitioner/{guid}/job/"
    result = nlx.extract(url, "<html></html>")
    assert result.title == "Nurse Practitioner"
    assert result.company_name == "DispatchHealth"
    assert result.location == "Denver, CO"
    assert result.employment_type == "full_time"
    assert result.workplace_type == "remote"
    assert result.posted_at.isoformat() == "2026-01-05"
    assert result.description == "Care for patients **virtually**."


def test_extract_none_for_unrelated_url(monkeypatch):
    def unexpected(*a, **k):
        raise AssertionError("Unexpected API request")
    monkeypatch.setattr(nlx.httpx, "get", unexpected)
    assert nlx.extract("https://example.com/jobs/1", "<html></html>") is None
