from app.services.adapters import google


def test_extract_sets_company_name_from_url_even_without_qualifications_body():
    html = "<meta name=\"description\" content=\"About the job blurb only\">"
    result = google.extract("https://www.google.com/about/careers/applications/jobs/results/12345", html)
    assert result.company_name == "Google"
    assert result.description is None


def test_extract_pulls_qualifications_body_up_to_boilerplate_marker():
    html = (
        "<h3>Minimum Qualifications</h3><p>Bachelor's degree</p>"
        '<div class="bE3reb">Legal/EEO boilerplate</div>'
    )
    result = google.extract("https://www.google.com/about/careers/applications/jobs/results/12345", html)
    assert result.company_name == "Google"
    assert "Minimum Qualifications" in result.description
    assert "Bachelor's degree" in result.description
    assert "Legal/EEO boilerplate" not in result.description


def test_extract_description_none_when_boilerplate_marker_missing():
    html = "<h3>Minimum Qualifications</h3><p>Bachelor's degree</p>"
    result = google.extract("https://www.google.com/about/careers/applications/jobs/results/12345", html)
    assert result.company_name == "Google"
    assert result.description is None


def test_extract_none_for_unrelated_page():
    html = "<h3>Minimum Qualifications</h3><p>Bachelor's degree</p>"
    assert google.extract("https://example.com/jobs/1", html) is None
