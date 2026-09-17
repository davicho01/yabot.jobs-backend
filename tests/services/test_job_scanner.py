from app.services.job_scanner import _html_to_formatted_text


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
