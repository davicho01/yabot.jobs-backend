import json

import pytest

from app.services import job_scanner
from app.services.adapters import base, stripe
from tests.conftest import FakeResponse

URL = "https://stripe.com/careers/listing/engineer/123"
NOTICE = (
    "The application window will remain open for 100 days after the Job Post is published. "
    "However, this opportunity will remain open based on the needs of the business, which may "
    "cause the application window to close before or after the 100-day mark."
)


@pytest.mark.parametrize("host", ["stripe.com", "www.stripe.com"])
def test_stripe_excludes_application_buttons_but_retains_full_notices(host):
    html = f'''<div class="careers-listing-closing">
    <p>We encourage you to apply.</p>
    <a href="/careers/apply/engineer/123"><span>Apply now</span><svg></svg></a>
    <a href="https://www.stripe.com/careers/apply/engineer/123?source=careers">Apply for this role</a>
    </div><div class="careers-listing-disclaimer"><div class="careers-listing-disclaimer__content">
    <p>Please find our California applicant personal information notice
    <a href="/legal/california-applicant-notice">here</a>.</p><p>{NOTICE}</p>
    </div></div>'''
    result = stripe.extract(f"https://{host}/careers/listing/engineer/123", html)
    description = result.description
    assert "Apply now" not in description
    assert "Apply for this role" not in description
    assert "/careers/apply/" not in description
    assert "We encourage you to apply." in description
    assert "Please find our California applicant personal information notice" in description
    assert f"[here](https://{host}/legal/california-applicant-notice)" in description
    assert NOTICE in description
    assert description.count(NOTICE) == 1


@pytest.mark.parametrize("payload", ["{bad", "null", "[]", "{}",
    json.dumps({"props": {"pageProps": {"listing": {"locations": None}}}})])
def test_stripe_keeps_description_when_location_payload_is_unusable(payload):
    html = '<div class="careers-listing-details__body"><p>Complete job description</p></div>'
    html += f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'
    result = stripe.extract(URL, html)
    assert result.description == "Complete job description"
    assert result.location is None


def test_stripe_caps_location_to_database_column_length():
    names = [f"Office {i} " + "X" * 40 for i in range(10)]
    payload = {"props": {"pageProps": {"listing": {"locations": [{"name": n} for n in names]}}}}
    html = f'<script id="__NEXT_DATA__">{json.dumps(payload)}</script>'
    result = stripe.extract(URL, html)
    assert result.description is None
    assert len(result.location) == 255
    assert result.location.endswith("...")
    assert result.location.startswith(names[0])


@pytest.mark.parametrize("url", ["https://example.com/job", "https://stripe.com.example.com/job"])
def test_stripe_markup_does_not_override_other_sites(url):
    html = '<div class="careers-listing-details__body">Unrelated content</div>'
    assert stripe.extract(url, html) is None


def test_stripe_scan_preserves_sections_and_all_locations(monkeypatch):
    ld = {"@type": "JobPosting", "title": "Engineer", "description": "Core duties",
          "jobLocation": {"address": {"addressLocality": "Toronto"}},
          "baseSalary": {"currency": "USD", "value": {"minValue": 224000, "maxValue": 336000}}}
    data = {"props": {"pageProps": {"listing": {"locations": [
        {"name": "Toronto"}, {"name": "Remote in Canada"}, {"name": "Seattle"},
        {"name": "Remote in United States"}, {"name": "Toronto"},
    ]}}}}
    html = f'''<script type="application/ld+json">{json.dumps(ld)}</script>
    <script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>
    <nav>Product navigation</nav>
    <div class="careers-listing-details__body"><div><h2>Responsibilities</h2><p>Core duties</p></div></div>
    <div class="careers-listing-details__body"><h2>Hybrid work at Stripe</h2><p>35 miles from an office</p></div>
    <div class="careers-listing-details__body"><h2>Pay and benefits</h2><p>Equity and medical benefits</p></div>
    <div class="careers-listing-closing"><p>We encourage you to apply.</p><a href="/careers/apply/engineer/123"><span>Apply now</span></a></div>
    <div class="careers-listing-disclaimer"><div><a href="/legal/notice">Applicant notice</a></div><p>100 days</p></div>
    <div class="careers-listing-details__sidebar-content"><h3>Team</h3><p>Data Platform</p><a href="https://stripe.com/careers/apply/engineer/123">Apply for this role</a></div>
    <footer>Marketing footer</footer>'''
    monkeypatch.setattr(base, "fetch_html", lambda _: FakeResponse(text=html, url=URL))
    result = job_scanner.scan_job_url(URL)
    assert result.success
    assert result.location == "Toronto; Remote in Canada; Seattle; Remote in United States"
    for text in ["Core duties", "35 miles", "Equity and medical", "100 days", "Data Platform",
                 "[Applicant notice](https://stripe.com/legal/notice)"]:
        assert text in result.description
    assert result.description.count("Core duties") == 1
    assert "Product navigation" not in result.description
    assert "Marketing footer" not in result.description
    assert "Apply now" not in result.description
    assert "Apply for this role" not in result.description
    assert "/careers/apply/" not in result.description
    assert "We encourage you to apply." in result.description
    assert (result.salary_min, result.salary_max, result.salary_currency) == (224000, 336000, "USD")
    assert stripe.extract("https://example.com/job", html) is None


def test_stripe_scan_falls_back_when_page_sections_or_payload_are_missing(monkeypatch):
    html = '''<script type="application/ld+json">{"@type":"JobPosting","title":"Engineer",
    "description":"Original description","jobLocation":{"address":{"addressLocality":"Toronto"}}}</script>
    <script id="__NEXT_DATA__">invalid json</script>'''
    monkeypatch.setattr(base, "fetch_html", lambda _: FakeResponse(text=html, url=URL))
    result = job_scanner.scan_job_url(URL)
    assert result.description == "Original description"
    assert result.location == "Toronto"
