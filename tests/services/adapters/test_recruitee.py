import pytest

from app.models.enums import EmploymentType, WorkplaceType
from app.services.adapters import base, recruitee
from tests.conftest import FakeResponse

OFFER = {
    "slug": "customer-success-specialist-3",
    "title": "Customer Success Specialist",
    "company_name": "Entyre Inc",
    "location": "Detroit, Michigan, United States",
    "description": "<p>" + "Help customers succeed. " * 15 + "</p>",
    "requirements": "<ul><li>Two years in support</li></ul>",
    "hybrid": True,
    "remote": False,
    "on_site": False,
    "employment_type_code": "fulltime_permanent",
    "published_at": "2026-07-01 14:00:00 UTC",
}


@pytest.fixture
def api(monkeypatch):
    requested = []

    def fake_get(url, **kwargs):
        requested.append(url)
        return FakeResponse(json_data={"offers": [OFFER]})

    monkeypatch.setattr(recruitee, "get_with_retry", fake_get)
    return requested


def test_offer_is_read_from_the_boards_api(api):
    result = recruitee.scan_job_url("https://entyreinc.recruitee.com/o/customer-success-specialist-3")

    assert api == ["https://entyreinc.recruitee.com/api/offers/"]
    assert result.success and (result.title, result.company_name) == ("Customer Success Specialist", "Entyre Inc")
    assert "Help customers succeed." in result.description and "Two years in support" in result.description
    assert (result.workplace_type, result.employment_type) == (WorkplaceType.HYBRID, EmploymentType.FULL_TIME)
    assert str(result.posted_at) == "2026-07-01"


def test_custom_domain_uses_the_crawl_sources_board(api):
    with base.scan_board_hint("https://entyreinc.recruitee.com"):
        result = recruitee.scan_job_url("https://careers.entyre.com/o/customer-success-specialist-3")

    assert api == ["https://entyreinc.recruitee.com/api/offers/"]
    assert result.success


def test_custom_domain_without_a_board_is_left_alone(api):
    assert recruitee.scan_job_url("https://careers.entyre.com/o/customer-success-specialist-3") is None


def test_offer_missing_from_the_board_is_expired(api):
    result = recruitee.scan_job_url("https://entyreinc.recruitee.com/o/some-filled-role")
    assert (result.success, result.expired) == (False, True)
