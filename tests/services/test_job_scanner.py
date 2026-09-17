from app.models.enums import EmploymentType, WorkplaceType
from app.services import job_scanner
from app.services.job_scanner import (
    _WORKDAY_JOB_URL_RE,
    _fetch_nlx_job_data,
    _html_to_formatted_text,
    _nlx_description_of,
    _nlx_employment_type_of,
    _nlx_posted_at_of,
    _nlx_workplace_type_of,
    _salary_from_text,
)
from tests.conftest import FakeResponse


def test_html_to_formatted_text_strips_normal_html():
    # The common case: real HTML tags, entities only inside text content —
    # must still convert correctly now that unescape runs first.
    html = "<p>Tom &amp; Jerry are <b>friends</b>.</p><ul><li>One</li><li>Two</li></ul>"
    result = _html_to_formatted_text(html)
    assert result == "Tom & Jerry are **friends**.\n\n- One\n- Two"


def test_html_to_formatted_text_handles_double_encoded_html():
    # The Freedom Mortgage / Phenom bug: the JSON-LD description is itself
    # HTML-entity-escaped, so the "tags" are literal "&lt;p&gt;" text, not
    # real "<p>" characters, until unescaped. Regression test for the fix in
    # app/services/job_scanner.py's _html_to_formatted_text: unescape must
    # run BEFORE tag stripping, or the tags only become real *after*
    # stripping and leak straight into the "Markdown" output.
    html = "&lt;p&gt;&lt;b&gt;&lt;span&gt;Summary&lt;/span&gt;&lt;/b&gt;&lt;span&gt;:&lt;/span&gt;&lt;/p&gt;&lt;p&gt;Body text.&lt;/p&gt;"
    result = _html_to_formatted_text(html)
    assert result is not None
    assert "<" not in result and ">" not in result
    assert "**Summary**" in result
    assert "Body text." in result


def test_html_to_formatted_text_none_for_non_string():
    assert _html_to_formatted_text(None) is None
    assert _html_to_formatted_text(123) is None


def test_html_to_formatted_text_none_for_blank_result():
    assert _html_to_formatted_text("   ") is None


def test_workday_job_url_re_matches_bare_url():
    match = _WORKDAY_JOB_URL_RE.search(
        "https://stryker.wd1.myworkdayjobs.com/StrykerCareers/job/Kalamazoo-Michigan/Senior-Engineer_R570975-1"
    )
    assert match is not None
    assert match.groups() == (
        "stryker",
        "wd1",
        "StrykerCareers",
        "/job/Kalamazoo-Michigan/Senior-Engineer_R570975-1",
    )


def test_workday_job_url_re_stops_at_href_quote_when_embedded_in_html():
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
    match = _WORKDAY_JOB_URL_RE.search(html)
    assert match is not None
    company, instance, site, job_path = match.groups()
    assert (company, instance, site) == ("stryker", "wd1", "StrykerCareers")
    assert job_path == "/job/Kalamazoo-Michigan/Senior-Engineer_R570975-1"
    assert "\n" not in job_path
    assert '"' not in job_path


def test_fetch_nlx_job_data_none_when_url_does_not_match_shape():
    assert _fetch_nlx_job_data("https://careers.dispatchhealth.com/job-categories/nursing/jobs/") is None


def test_fetch_nlx_job_data_fetches_by_host_and_guid(monkeypatch):
    # NLX job pages carry no JSON-LD/og: data of their own (see nlx.py) — the
    # real record lives in a static per-job JSON keyed by the guid already
    # in the URL, verified live via the browser network tab.
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResponse(json_data={"title": "Senior Platform Engineer"})

    monkeypatch.setattr(job_scanner.httpx, "get", fake_get)
    guid = "D1C3F4BB180D40F1A8E36535C29DB20D"
    data = _fetch_nlx_job_data(f"https://careers.dispatchhealth.com/virtual-usa/senior-platform-engineer/{guid}/job/")
    assert data == {"title": "Senior Platform Engineer"}
    assert calls == [f"https://microsites.dejobs.org/careers-dispatchhealth-com/data/{guid}.json"]


def test_fetch_nlx_job_data_none_on_http_error(monkeypatch):
    monkeypatch.setattr(job_scanner.httpx, "get", lambda *a, **k: FakeResponse(status_code=404))
    guid = "D1C3F4BB180D40F1A8E36535C29DB20D"
    assert _fetch_nlx_job_data(f"https://careers.dispatchhealth.com/x/y/{guid}/job/") is None


def test_nlx_employment_type_of_maps_known_values():
    assert _nlx_employment_type_of({"job_type": "Full Time"}) == EmploymentType.FULL_TIME
    assert _nlx_employment_type_of({"job_type": "Part Time"}) == EmploymentType.PART_TIME


def test_nlx_employment_type_of_unknown_for_per_diem():
    # "Per Diem" (healthcare as-needed work, verified live) doesn't map
    # cleanly to any EmploymentType value — must fall back to UNKNOWN
    # rather than guessing.
    assert _nlx_employment_type_of({"job_type": "Per Diem"}) == EmploymentType.UNKNOWN


def test_nlx_workplace_type_of_maps_known_values():
    assert _nlx_workplace_type_of({"job_shift": "Remote"}) == WorkplaceType.REMOTE
    assert _nlx_workplace_type_of({"job_shift": "On-site"}) == WorkplaceType.ONSITE
    assert _nlx_workplace_type_of({"job_shift": "Hybrid"}) == WorkplaceType.HYBRID


def test_nlx_posted_at_of_parses_iso_timestamp():
    assert _nlx_posted_at_of({"date_added": "2026-09-10T21:52:37.548Z"}).isoformat() == "2026-09-10"


def test_nlx_posted_at_of_none_when_missing_or_malformed():
    assert _nlx_posted_at_of({}) is None
    assert _nlx_posted_at_of({"date_added": "not-a-date"}) is None


def test_nlx_description_of_converts_html():
    assert _nlx_description_of({"html_description": "<p>Hello <b>world</b></p>"}) == "Hello **world**"


def test_nlx_description_of_none_when_blank():
    assert _nlx_description_of({"html_description": "  "}) is None
    assert _nlx_description_of({}) is None


def test_salary_from_text_parses_k_suffix_range():
    # The DispatchHealth/NLX bug: "$145k-$163k" has no thousands-grouping
    # comma and a trailing k/K multiplier — before the fix, the amount
    # group matched just "145" and then failed to find a dash immediately
    # after (blocked by the literal "k"), silently dropping the whole
    # range instead of parsing it.
    assert _salary_from_text("Base Salary Range: $145k-$163k") == (145000, 163000, "USD")


def test_salary_from_text_still_parses_full_digit_range():
    assert _salary_from_text("Pay Range: $120,000-$150,000") == (120000, 150000, "USD")


def test_salary_from_text_none_without_currency_signal():
    assert _salary_from_text("We need 8-10 years of experience") == (None, None, None)
