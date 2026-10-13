import json

import httpx
import pytest

from app.models.enums import EmploymentType
from app.services.adapters import base, personio
from app.services.adapters.base import FetchedPage

JOB_URL = "https://jungvonmatt.jobs.personio.de/job/2759054"

JOB = {
    "id": "2759054",
    "name": "(Senior) Concept Creative Social Media (d/f/m)",
    "employment_type_slug": "permanent",
    "schedule_slug": "full-time",
    "fields": [
        {"label": "Position", "value": "$18"},
        {"label": "Dein Profil", "value": "<p>Mindestens 3 Jahre Erfahrung.</p>"},
        {"label": "Warum wir?", "value": ""},
    ],
    "main_office": "Hamburg",
    "published_at": "2026-08-18T16:18:14Z",
    "office_addresses": [{"name": "Hamburg", "city": "Hamburg", "country": "DE"}],
}
POSITION_HTML = "<p>Du entwickelst Kampagnen für Social Media – ab 4.000 € im Monat.</p>"


def _page(job=JOB, company="Jung von Matt", text_rows=None) -> str:
    """A job page as Personio serves it: the RSC payload split over several
    self.__next_f.push chunks, with long section values as "<id>:T<hex byte
    length>," text rows (byte length, so the umlaut and € count double)."""
    text_rows = {"18": POSITION_HTML} if text_rows is None else text_rows
    rows = "".join(f"{key}:T{len(value.encode()):x},{value}" for key, value in text_rows.items())
    data = json.dumps(
        {"currentHost": "jungvonmatt.jobs.personio.de", "job": job, "careerSiteSettings": {"company_name": company}},
        separators=(",", ":"),  # compact, as Personio serves it
    )
    payload = f'4:["$","main",null,{data}]\n{rows}5:["$","footer",null,{{}}]'
    middle = len(payload) // 2
    chunks = [payload[:middle], payload[middle:]]
    scripts = "".join(f"<script>self.__next_f.push([1,{json.dumps(chunk)}])</script>" for chunk in chunks)
    teaser = '<meta property="og:description" content="Du entwickelst Kampagnen..."/>'
    return f"<html><head>{teaser}</head><body>{scripts}</body></html>"


@pytest.fixture
def serve(monkeypatch):
    def _serve(html):
        monkeypatch.setattr(base, "fetch_html", lambda url: FetchedPage(text=html, url=url))
    return _serve


def test_job_is_read_from_the_embedded_page_data(serve):
    serve(_page())

    result = personio.scan_job_url(JOB_URL)

    assert result.success
    assert result.title == "(Senior) Concept Creative Social Media (d/f/m)"
    assert result.company_name == "Jung von Matt"
    assert result.location == "Hamburg, Germany"
    assert result.employment_type == EmploymentType.FULL_TIME
    assert str(result.posted_at) == "2026-08-18"
    assert "Kampagnen für Social Media – ab 4.000 € im Monat." in result.description  # the "$18" text row
    assert "Dein Profil" in result.description and "Mindestens 3 Jahre Erfahrung." in result.description
    assert "Warum wir?" not in result.description  # empty sections are left out


@pytest.mark.parametrize(
    "employment, schedule, expected",
    [
        ("intern", "full-time", EmploymentType.INTERNSHIP),
        ("freelance", "part-time", EmploymentType.CONTRACT),
        ("permanent", "part-time", EmploymentType.PART_TIME),
        ("permanent", "full-or-part-time", EmploymentType.UNKNOWN),
    ],
)
def test_employment_type(serve, employment, schedule, expected):
    serve(_page({**JOB, "employment_type_slug": employment, "schedule_slug": schedule}))

    assert personio.scan_job_url(JOB_URL).employment_type == expected


def test_several_offices_and_a_blank_company_name(serve):
    offices = [{"city": "Žilina", "country": "SK"}, {"city": "Bratislava", "country": "SK"}]
    serve(_page({**JOB, "office_addresses": offices}, company=""))

    result = personio.scan_job_url(JOB_URL)

    assert result.location == "Žilina, Slovakia; Bratislava, Slovakia"
    assert result.company_name is None


def test_page_without_job_data_fails_without_expiring(serve):
    serve("<html><head><title>Jobs</title></head></html>")

    result = personio.scan_job_url(JOB_URL)

    assert (result.success, result.expired) == (False, False)


def test_removed_job_page_is_gone(monkeypatch):
    def gone(url):
        raise base.PostingGone(url, 404)

    monkeypatch.setattr(base, "fetch_html", gone)

    result = personio.scan_job_url(JOB_URL)

    assert not result.success and result.error.startswith(base.GONE_ERROR_PREFIX)  # job_scanner expires it


def test_other_urls_are_not_claimed():
    assert personio.scan_job_url("https://jungvonmatt.jobs.personio.de/") is None
    assert personio.scan_job_url("https://example.com/job/2759054") is None


def test_fetch_error_fails_without_expiring(monkeypatch):
    def down(url):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(base, "fetch_html", down)

    result = personio.scan_job_url(JOB_URL)

    assert (result.success, result.expired) == (False, False)
