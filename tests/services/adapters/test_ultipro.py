import json

from app.models.enums import EmploymentType
from app.services.adapters import base, ultipro

URL = (
    "https://recruiting2.ultipro.com/GRA1027GAMH/JobBoard/3ebfa49f-f33c-45e6-a36a-a363f8b76be3/"
    "OpportunityDetail?opportunityId=e0b53238-1c8f-4290-aa0b-11e3a976c989"
)
OPPORTUNITY = {
    "Id": "e0b53238-1c8f-4290-aa0b-11e3a976c989",
    "Title": "Houseman Full- Time",
    "FullTime": True,
    "Description": "<p>" + "Support the housekeeping team. " * 12 + "</p>",
    "PostedDate": "2026-08-31T18:37:03.82Z",
    "OpportunityIsClosed": False,
    "Locations": [{"Address": {"City": "Salt Lake City", "State": {"Name": "Utah", "Code": "UT"},
                               "Country": {"Name": "United States", "Code": "USA"}}}],
}


def _page(opportunity) -> str:
    return (
        "<script>$(function () { var opportunity = new US.Opportunity.CandidateOpportunityDetail("
        f"{json.dumps(opportunity)}); opportunity.init(); }});</script>"
    )


def _scan(monkeypatch, html):
    monkeypatch.setattr(base, "fetch_html", lambda u: base.FetchedPage(text=html, url=u))
    monkeypatch.setattr(ultipro, "fetch_rendered_html", lambda *a, **k: None)
    return ultipro.scan_job_url(URL)


def test_job_is_read_from_the_embedded_opportunity(monkeypatch):
    result = _scan(monkeypatch, _page(OPPORTUNITY))

    assert result.success and result.title == "Houseman Full- Time"
    assert "Support the housekeeping team." in result.description
    assert result.location == "Salt Lake City, UT, United States"
    assert (result.employment_type, str(result.posted_at)) == (EmploymentType.FULL_TIME, "2026-08-31")


def test_closed_opportunity_is_expired(monkeypatch):
    result = _scan(monkeypatch, _page({**OPPORTUNITY, "OpportunityIsClosed": True}))
    assert (result.success, result.expired) == (False, True)


def test_page_without_the_opportunity_falls_back_to_rendering(monkeypatch):
    result = _scan(monkeypatch, "<html></html>")
    assert result.success is False and "render" in result.error.lower()
