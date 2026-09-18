from app.services import job_scanner
from app.services.adapters import base
from app.services.adapters.text import html_to_formatted_text as _html_to_formatted_text
from tests.conftest import FakeResponse

_salary_from_text = base.salary_from_text


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
    # app/services/adapters/text.py's html_to_formatted_text: unescape must
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


def test_scan_job_url_dispatches_to_the_matching_adapter(monkeypatch):
    # Amazon's own scan_job_url always sets company_name="Amazon" — if
    # dispatch instead fell through to the generic default scanner, this
    # page's (deliberately wrong) og:site_name would win instead, proving
    # the two paths were mixed rather than one adapter owning the result.
    html = '<meta property="og:site_name" content="Totally Wrong Site"/>'
    monkeypatch.setattr(
        base, "fetch_html", lambda _url: FakeResponse(text=html, url="https://www.amazon.jobs/en/jobs/123")
    )
    result = job_scanner.scan_job_url("https://www.amazon.jobs/en/jobs/123")
    assert result.success
    assert result.company_name == "Amazon"


def test_scan_job_url_falls_back_to_default_scanner_when_no_adapter_matches(monkeypatch):
    html = '<script type="application/ld+json">{"@type": "JobPosting", "title": "Staff Engineer"}</script>'
    monkeypatch.setattr(base, "fetch_html", lambda _url: FakeResponse(text=html, url="https://example.com/careers/1"))
    result = job_scanner.scan_job_url("https://example.com/careers/1")
    assert result.success
    assert result.title == "Staff Engineer"
