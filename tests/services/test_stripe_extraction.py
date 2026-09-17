import json

import pytest

from app.services.job_scanner import _stripe_job_details

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
    description, _ = _stripe_job_details(f"https://{host}/careers/listing/engineer/123", html)
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
    assert _stripe_job_details(URL, html) == ("Complete job description", None)


def test_stripe_caps_location_to_database_column_length():
    names = [f"Office {i} " + "X" * 40 for i in range(10)]
    payload = {"props": {"pageProps": {"listing": {"locations": [{"name": n} for n in names]}}}}
    html = f'<script id="__NEXT_DATA__">{json.dumps(payload)}</script>'
    description, location = _stripe_job_details(URL, html)
    assert description is None
    assert len(location) == 255
    assert location.endswith("...")
    assert location.startswith(names[0])


@pytest.mark.parametrize("url", ["https://example.com/job", "https://stripe.com.example.com/job"])
def test_stripe_markup_does_not_override_other_sites(url):
    html = '<div class="careers-listing-details__body">Unrelated content</div>'
    assert _stripe_job_details(url, html) == (None, None)
