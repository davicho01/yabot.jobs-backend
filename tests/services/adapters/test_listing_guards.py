import pytest

from app.services.adapters import base, hirehive, jazzhr, jobvite, peopleadmin, zenats
from tests.conftest import FakeResponse

# Each regex scraper may call a board empty only when the page is recognizably
# the platform's listing (see base.require_listing_page): a 200 block, login
# or changed-layout shell must raise, not record a clean zero-job crawl.
# (module, listing function, board key, marker an empty listing page carries)
SCRAPERS = [
    (jobvite, "_fetch_jobs", "acme", "jv-job-list"),
    (jazzhr, "_fetch_jobs", "acme", "resumator"),
    (hirehive, "_fetch_jobs", "acme.hirehive.com", "hirehive"),
    (peopleadmin, "_fetch_jobs", "acme.peopleadmin.com", "peopleadmin"),
    (zenats, "_fetch_jobs", "jobs.acme.com", "zenats"),
]
IDS = [s[0].__name__.rsplit(".", 1)[1] for s in SCRAPERS]


def _serve(monkeypatch, module, text):
    monkeypatch.setattr(module, "get_with_retry", lambda url, **kwargs: FakeResponse(text=text))


@pytest.mark.parametrize("module,fn,key,marker", SCRAPERS, ids=IDS)
def test_a_listing_page_with_no_jobs_is_an_empty_board(monkeypatch, module, fn, key, marker):
    _serve(monkeypatch, module, f"<html><div class='{marker}'>No open positions</div></html>")

    assert getattr(module, fn)(key) == []


@pytest.mark.parametrize("module,fn,key,marker", SCRAPERS, ids=IDS)
def test_a_page_that_is_not_the_listing_is_an_error(monkeypatch, module, fn, key, marker):
    _serve(monkeypatch, module, "<html><title>Just a moment...</title>Checking your browser</html>")

    with pytest.raises(ValueError):
        getattr(module, fn)(key)


def test_the_marker_check_ignores_case():
    base.require_listing_page("<div>JV-JOB-LIST</div>", "jv-job-list", "board")
