from app.services.adapters import base, rcwilley
from tests.conftest import FakeResponse

# Trimmed from a live rcwilley.com job page: the JSON-LD description holds a
# raw newline (invalid strict JSON) and "title" is the department.
_JOB_HTML = """<html><head><title>IT Security Analyst | RC Willey</title>
<script type="application/ld+json">{
  "@context": "https://schema.org", "@type": "JobPosting", "title": "Corporate",
  "datePosted": "2026-09-09", "description": "IT Security Analyst
RC Willey is looking for a cybersecurity professional.",
  "employmentType": "FULL_TIME",
  "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress",
    "addressLocality": "Salt Lake City", "addressRegion": "UT", "addressCountry": "US"}},
  "baseSalary": {"@type": "MonetaryAmount", "currency": "USD",
    "value": {"@type": "QuantitativeValue", "minValue": "75000.0", "maxValue": "95000.0"}}
}</script></head><body><h1>IT Security Analyst</h1></body></html>"""


def test_json_ld_tolerates_raw_control_characters():
    postings = base.extract_json_ld_postings(_JOB_HTML)
    assert postings[0]["description"].startswith("IT Security Analyst\nRC Willey")


def test_scan_uses_h1_title_not_json_ld_department(monkeypatch):
    url = "https://www.rcwilley.com/job/Corporate-Office/33871/IT-Security-Analyst"
    monkeypatch.setattr(base, "fetch_html", lambda u: base.FetchedPage(text=_JOB_HTML, url=u))
    result = rcwilley.scan_job_url(url)
    assert result.title == "IT Security Analyst"
    assert result.location == "Salt Lake City, UT, US"
    assert (result.salary_min, result.salary_max) == (75000, 95000)


def test_scan_ignores_non_job_pages():
    assert rcwilley.scan_job_url("https://www.rcwilley.com/Furniture/Living-Room") is None


def test_fetch_jobs_dedupes_listing_links(monkeypatch):
    html = '<a href="/job/Orem-Utah-Store/1/Sales">a</a><a href="/job/Orem-Utah-Store/1/Sales">b</a>'
    monkeypatch.setattr(rcwilley, "get_with_retry", lambda url, timeout: FakeResponse(text=html))
    assert rcwilley._fetch_jobs("rcwilley") == ["https://www.rcwilley.com/job/Orem-Utah-Store/1/Sales"]
