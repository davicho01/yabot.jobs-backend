"""Sector assignment (app.services.job_sector) and its API client
(app.services.sector_api). The sector service itself is faked: no network."""

import itertools

import httpx
import pytest

import app.models as m
from app.core.config import settings
from app.models.enums import JobSector, ScanStatus
from app.services import job_sector, sector_api
from app.services.sector_api import SectorPrediction

_counter = itertools.count()
MODEL = "ettin32m-2026-10-08"


def _posting(scan_db, *, title="Registered Nurse", description="Provide patient care.", **fields) -> m.JobPosting:
    n = next(_counter)
    url_row = m.JobPostingUrl(
        url=f"https://example.com/jobs/sector-{n}",
        normalized_url=f"https://example.com/jobs/sector-{n}",
        url_hash=f"sector-hash-{n}",
        domain="example.com",
    )
    scan_db.add(url_row)
    scan_db.flush()
    posting = m.JobPosting(
        url_id=url_row.id, title=title, description=description, extraction_status=ScanStatus.SUCCESS, **fields
    )
    scan_db.add(posting)
    scan_db.commit()
    return posting


@pytest.fixture
def api(monkeypatch):
    """Fake sector API: answers from `answers` (title -> prediction or None), counts calls."""
    state = {"calls": 0, "answers": {}}

    def fake(title, description):
        state["calls"] += 1
        return state["answers"].get(title, SectorPrediction("healthcare", 0.98, MODEL))

    monkeypatch.setattr(job_sector, "classify_remote", fake)
    return state


class TestApplySector:
    def test_stores_sector_confidence_model_and_hash(self, scan_db, api):
        posting = _posting(scan_db)
        job_sector.apply_sector(posting, posting.title, posting.description)
        assert posting.sector == JobSector.HEALTHCARE
        assert posting.sector_confidence == 0.98
        assert posting.sector_model == MODEL
        assert posting.sector_input_hash == job_sector.input_hash(posting.title, posting.description)

    def test_below_threshold_is_unknown_but_keeps_the_confidence(self, scan_db, api, monkeypatch):
        monkeypatch.setattr(settings, "sector_confidence_threshold", 0.7)
        api["answers"]["Specialist"] = SectorPrediction("sales", 0.55, MODEL)
        posting = _posting(scan_db, title="Specialist")
        job_sector.apply_sector(posting, posting.title, posting.description)
        assert posting.sector == JobSector.UNKNOWN
        assert posting.sector_confidence == 0.55
        assert posting.sector_model == MODEL  # classified, not pending

    def test_api_failure_leaves_the_posting_pending(self, scan_db, api):
        api["answers"]["Registered Nurse"] = None
        posting = _posting(scan_db, sector=JobSector.SALES, sector_model="keyword-matcher")
        job_sector.apply_sector(posting, posting.title, posting.description)
        assert posting.sector == JobSector.UNKNOWN
        assert posting.sector_model is None and posting.sector_confidence is None and posting.sector_input_hash is None

    def test_rescan_with_unchanged_text_skips_the_api(self, scan_db, api):
        posting = _posting(scan_db)
        job_sector.apply_sector(posting, posting.title, posting.description)
        job_sector.apply_sector(posting, posting.title, posting.description)
        assert api["calls"] == 1

    def test_rescan_with_changed_text_calls_the_api_again(self, scan_db, api):
        posting = _posting(scan_db)
        job_sector.apply_sector(posting, posting.title, posting.description)
        job_sector.apply_sector(posting, posting.title, "Now a completely different job.")
        assert api["calls"] == 2

    def test_unknown_label_from_the_model_is_stored_as_unknown(self, scan_db, api):
        api["answers"]["Odd"] = SectorPrediction("not_a_sector", 0.99, MODEL)
        posting = _posting(scan_db, title="Odd")
        job_sector.apply_sector(posting, posting.title, posting.description)
        assert posting.sector == JobSector.UNKNOWN


class TestClassifyPendingSectors:
    def test_classifies_only_pending_postings(self, scan_db, api):
        pending = _posting(scan_db, sector_model=None)
        done = _posting(scan_db, title="Old", sector=JobSector.SALES, sector_model="keyword-matcher")
        classified, still_pending = job_sector.classify_pending_sectors(scan_db, workers=1)
        assert (classified, still_pending) == (1, 0)
        assert pending.sector == JobSector.HEALTHCARE and pending.sector_model == MODEL
        assert done.sector == JobSector.SALES and done.sector_model == "keyword-matcher"
        assert api["calls"] == 1

    def test_stops_when_the_service_is_down(self, scan_db, api, monkeypatch):
        monkeypatch.setattr(job_sector, "classify_remote", lambda *_: None)
        for _ in range(3):
            _posting(scan_db, sector_model=None)
        classified, still_pending = job_sector.classify_pending_sectors(scan_db, workers=1)
        assert classified == 0 and still_pending == 3


class TestSectorApiClient:
    class _Response:
        def __init__(self, payload, status=200):
            self._payload, self.status_code = payload, status

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("error", request=httpx.Request("POST", "x"), response=httpx.Response(self.status_code))

        def json(self):
            return self._payload

    def test_not_configured_returns_none_without_calling(self, monkeypatch):
        monkeypatch.setattr(settings, "sector_api_url", None)
        monkeypatch.setattr(sector_api.httpx, "post", lambda *a, **k: pytest.fail("should not call"))
        assert sector_api.classify_remote("Nurse", "Care") is None

    def test_parses_the_response(self, monkeypatch):
        monkeypatch.setattr(settings, "sector_api_url", "https://sector.example")
        monkeypatch.setattr(sector_api, "_identity_token_headers", lambda _aud: {})
        sent = {}

        def fake_post(url, json, headers, timeout):
            sent.update(url=url, json=json)
            return self._Response({"sector": "healthcare", "confidence": 0.97, "model": MODEL})

        monkeypatch.setattr(sector_api.httpx, "post", fake_post)
        assert sector_api.classify_remote("Nurse", "<p>Care</p>") == SectorPrediction("healthcare", 0.97, MODEL)
        assert sent == {"url": "https://sector.example/classify", "json": {"title": "Nurse", "description": "<p>Care</p>"}}

    @pytest.mark.parametrize(
        "behavior",
        [
            lambda: (_ for _ in ()).throw(httpx.ConnectTimeout("timed out")),
            lambda: TestSectorApiClient._Response({}, status=503),
            lambda: TestSectorApiClient._Response({"unexpected": "shape"}),
        ],
    )
    def test_any_failure_returns_none(self, monkeypatch, behavior):
        monkeypatch.setattr(settings, "sector_api_url", "https://sector.example")
        monkeypatch.setattr(sector_api, "_identity_token_headers", lambda _aud: {})
        monkeypatch.setattr(sector_api.httpx, "post", lambda *a, **k: behavior())
        assert sector_api.classify_remote("Nurse", "Care") is None
