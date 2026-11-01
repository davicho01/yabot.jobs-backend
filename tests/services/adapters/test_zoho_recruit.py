import html
import json
from datetime import date

import pytest

from app.models.enums import EmploymentType, WorkplaceType
from app.services.adapters import base, zoho_recruit

HOST = "acme.zohorecruit.com"


def _job(job_id, title, *, publish=True, locked=False, **extra):
    return {
        "id": job_id,
        "Posting_Title": title,
        "Publish": publish,
        "Is_Locked": locked,
        "City": "Austin",
        "State": "Texas",
        "Country": "United States",
        "Job_Type": "Full time",
        "Date_Opened": "2026-09-01",
        **extra,
    }


def _board_page(jobs):
    value = html.escape(json.dumps(jobs), quote=True)
    return f'<html><input type="hidden" value="{value}" id="jobs"><input type="hidden" id="meta"></html>'


def _job_page(job, site_name="Acme Corp"):
    # Zoho inlines the record as a JS string literal: quotes as \x22, and
    # characters like "-" backslash-escaped.
    literal = json.dumps([job]).replace("\\", "\\\\").replace('"', "\\x22").replace("'", "\\'").replace("-", "\\-")
    return f"<meta property=\"og:site_name\" content=\"{site_name}\" />\n<script>var jobs = JSON.parse('{literal}');</script>"


class _Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def _fetch(monkeypatch, page):
    monkeypatch.setattr(zoho_recruit, "get_with_retry", lambda url, **kwargs: _Response(page))
    return zoho_recruit._fetch_jobs(HOST)


def test_match_takes_the_tenant_host_and_ignores_zohos_own_site():
    assert zoho_recruit._match("https://Acme.zohorecruit.com/jobs/Careers/123/x") == "acme.zohorecruit.com"
    assert zoho_recruit._match("https://acme.zohorecruit.eu") == "acme.zohorecruit.eu"
    assert zoho_recruit._match("https://acme.zohorecruit.com.au/jobs/Careers") == "acme.zohorecruit.com.au"
    assert zoho_recruit._match("https://www.zohorecruit.com/pricing") is None
    assert zoho_recruit._match("https://acme.example.com/jobs/Careers") is None


def test_only_published_unlocked_jobs_are_listed_by_id(monkeypatch):
    page = _board_page(
        [
            _job("111", "Engineer", Date_Opened="2026-09-02"),
            _job("222", "Old role", publish=False, locked=True),
            _job("333", "Locked", locked=True),
            _job("444", "Analyst"),
        ]
    )

    listing = _fetch(monkeypatch, page)

    assert listing.urls == [f"https://{HOST}/jobs/Careers/111", f"https://{HOST}/jobs/Careers/444"]
    assert listing.complete
    assert listing.posted[f"https://{HOST}/jobs/Careers/111"] == date(2026, 9, 2)


def test_an_empty_board_is_an_empty_listing(monkeypatch):
    assert _fetch(monkeypatch, _board_page([])).urls == []


def test_a_page_without_the_job_list_fails_loudly(monkeypatch):
    with pytest.raises(ValueError):
        _fetch(monkeypatch, "<html>Zoho maintenance page</html>")


def _scan(monkeypatch, page, url=f"https://{HOST}/jobs/Careers/111/Some-Old-Slug"):
    monkeypatch.setattr(base, "fetch_html", lambda u: base.FetchedPage(text=page, url=u))
    return zoho_recruit.scan_job_url(url)


def test_job_is_read_from_the_inlined_record(monkeypatch):
    job = _job(
        "111",
        "Forward Deployed Engineer",
        Job_Description="<p>Build things with customers.</p><ul><li>Python</li></ul>",
        Salary="$120,000 - $160,000 a year",
    )

    result = _scan(monkeypatch, _job_page(job))

    assert result.success
    assert (result.title, result.company_name) == ("Forward Deployed Engineer", "Acme Corp")
    assert result.location == "Austin, Texas, United States"
    assert result.employment_type == EmploymentType.FULL_TIME
    assert (result.salary_min, result.salary_max, result.salary_currency) == (120000, 160000, "USD")
    assert result.posted_at == date(2026, 9, 1)
    assert "Build things with customers" in result.description


def test_remote_city_is_remote_and_not_repeated(monkeypatch):
    job = _job("111", "Engineer", City="Remote", State="Remote", Job_Description="<p>x</p>")

    result = _scan(monkeypatch, _job_page(job))

    assert result.location == "Remote, United States"
    assert result.workplace_type == WorkplaceType.REMOTE


def test_an_unpublished_job_is_expired(monkeypatch):
    result = _scan(monkeypatch, _job_page(_job("111", "Engineer", publish=False, locked=True)))

    assert not result.success and result.expired


def test_a_page_for_another_job_id_is_not_read_as_this_one(monkeypatch):
    result = _scan(monkeypatch, _job_page(_job("999", "Someone else")))

    assert not result.success and not result.expired


def test_other_urls_are_not_claimed():
    assert zoho_recruit.scan_job_url(f"https://{HOST}/jobs/Careers") is None
    assert zoho_recruit.scan_job_url("https://www.zohorecruit.com/jobs/Careers/123") is None
    assert zoho_recruit.scan_job_url("https://boards.greenhouse.io/acme/jobs/1") is None
