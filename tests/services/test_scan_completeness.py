"""Incomplete-scan detection (app.services.scan_completeness) and how
_apply_scan_result stores an incomplete scan: visible, but retried like a
failed one and ending in needs_review if it never completes."""

from app.core.config import settings
from app.models import JobPosting, JobPostingUrl
from app.models.enums import ScanStatus
from app.services import job_scanner, jobs
from app.services.adapters.base import AtsAdapter, ScanResult
from app.services.scan_completeness import INCOMPLETE_SCAN_ERROR_PREFIX, incomplete_reasons

FULL = "We are hiring. " * 30  # 450 characters
TEASER = "Our client is an authorized distributor of Indofood products in the Philippines..."


def _incomplete(description=TEASER, **kwargs) -> ScanResult:
    result = ScanResult(success=True, title="Engineer", description=description, **kwargs)
    result.incomplete_reasons = incomplete_reasons(result)
    return result


def _complete(**kwargs) -> ScanResult:
    return ScanResult(success=True, title="Engineer", description=FULL, **kwargs)


# ------------------------------------------------------------- the rule


def test_full_description_is_complete():
    assert incomplete_reasons(_complete()) == []


def test_missing_description_is_incomplete():
    assert incomplete_reasons(ScanResult(success=True, title="Engineer")) == ["no description"]
    assert incomplete_reasons(ScanResult(success=True, title="Engineer", description="   ")) == ["no description"]


def test_short_description_is_incomplete():
    reasons = incomplete_reasons(ScanResult(success=True, title="Engineer", description="Durham, NC"))
    assert reasons == [f"description is 10 characters (under {settings.scan_min_description_chars})"]


def test_threshold_is_configurable(monkeypatch):
    monkeypatch.setattr(settings, "scan_min_description_chars", 50)
    assert incomplete_reasons(ScanResult(success=True, title="Engineer", description=TEASER)) == []


# ------------------------------------------------------ storing the result


def _posting(scan_db, url_row) -> JobPosting:
    return scan_db.query(JobPosting).filter_by(url_id=url_row.id).one()


def test_incomplete_scan_is_shown_but_retried(scan_db, make_source, make_url):
    url_row = make_url(make_source())

    jobs._apply_scan_result(scan_db, url_row, _incomplete(location="Taguig"))
    scan_db.commit()

    posting = _posting(scan_db, url_row)
    assert posting.extraction_status == ScanStatus.SUCCESS  # visible in search
    assert posting.description == TEASER and posting.location == "Taguig"
    url_row = scan_db.get(JobPostingUrl, url_row.id)
    assert url_row.scan_status == ScanStatus.FAILED  # picked up by the retry sweep
    assert url_row.scan_attempts == 1 and url_row.next_retry_at is not None
    assert url_row.scan_error.startswith(INCOMPLETE_SCAN_ERROR_PREFIX)


def test_incomplete_scan_ends_in_needs_review(scan_db, make_source, make_url):
    url_row = make_url(make_source())

    for _ in range(settings.scan_retry_max_attempts):
        jobs._apply_scan_result(scan_db, url_row, _incomplete())
        scan_db.commit()

    url_row = scan_db.get(JobPostingUrl, url_row.id)
    assert url_row.scan_status == ScanStatus.NEEDS_REVIEW and url_row.next_retry_at is None
    assert url_row.scan_error.startswith(INCOMPLETE_SCAN_ERROR_PREFIX)
    assert _posting(scan_db, url_row).extraction_status == ScanStatus.SUCCESS  # still shown


def test_incomplete_rescan_keeps_an_earlier_complete_posting(scan_db, make_source, make_url):
    url_row = make_url(make_source())
    jobs._apply_scan_result(scan_db, url_row, _complete(location="Atlanta, GA"))
    scan_db.commit()

    jobs._apply_scan_result(scan_db, url_row, _incomplete())
    scan_db.commit()

    posting = _posting(scan_db, url_row)
    assert posting.description == FULL and posting.location == "Atlanta, GA"
    assert scan_db.get(JobPostingUrl, url_row.id).scan_status == ScanStatus.FAILED


def test_complete_rescan_clears_the_incomplete_state(scan_db, make_source, make_url):
    url_row = make_url(make_source())
    jobs._apply_scan_result(scan_db, url_row, _incomplete())
    scan_db.commit()

    jobs._apply_scan_result(scan_db, url_row, _complete())
    scan_db.commit()

    url_row = scan_db.get(JobPostingUrl, url_row.id)
    assert url_row.scan_status == ScanStatus.SUCCESS
    assert url_row.scan_attempts == 0 and url_row.next_retry_at is None and url_row.scan_error is None
    assert _posting(scan_db, url_row).description == FULL


# ------------------------------------------------------------ the scanner


def _scan_with_adapter_result(monkeypatch, result: ScanResult) -> ScanResult:
    adapter = AtsAdapter("fake", match=lambda url: None, scan_job_url=lambda url: result)
    monkeypatch.setattr(job_scanner, "ADAPTERS", [adapter])
    return job_scanner.scan_job_url("https://example.com/jobs/1")


def test_scanner_marks_an_adapters_short_result_incomplete(monkeypatch):
    result = _scan_with_adapter_result(monkeypatch, ScanResult(success=True, title="Engineer", description=TEASER))
    assert result.incomplete_reasons and result.success


def test_scanner_leaves_complete_and_failed_results_alone(monkeypatch):
    assert _scan_with_adapter_result(monkeypatch, _complete()).incomplete_reasons == []
    failed = _scan_with_adapter_result(monkeypatch, ScanResult(success=False, error="blocked"))
    assert failed.incomplete_reasons == []
