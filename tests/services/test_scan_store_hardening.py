"""Storing a scanned page must survive what the web throws at it.

Regression tests for poison URLs seen in prod: a NUL character in the page
(Postgres rejects \\u0000 in text and JSONB — UntranslatableCharacter) and a
title longer than its varchar(255) column (StringDataRightTruncation) each
made the result UPDATE fail, so the scan message was retried forever.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import DataError, OperationalError

from app.models import JobPosting, JobPostingUrl
from app.models.enums import ScanStatus
from app.services import jobs
from app.services.adapters.base import ScanResult


def _contains_nul(value) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_contains_nul(k) or _contains_nul(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_nul(v) for v in value)
    return False


def test_strip_nul_cleans_nested_structures():
    dirty = {"a\x00": ["x\x00y", {"n": "1\x002"}], "num": 5, "none": None}

    clean = jobs._strip_nul(dirty)

    assert clean == {"a": ["xy", {"n": "12"}], "num": 5, "none": None}
    assert not _contains_nul(clean)


def test_fit_truncates_and_strips_and_passes_none_through():
    assert jobs._fit(None, 10) is None
    assert jobs._fit("ab\x00cd", 10) == "abcd"
    assert jobs._fit("x" * 300, 255) == "x" * 255


def test_upsert_stores_hostile_page_content_cleanly(scan_db, make_source, make_url):
    url_row = make_url(make_source())
    result = ScanResult(
        success=True,
        title="T" * 300 + "\x00",
        company_name="Co\x00mpany",
        location="L" * 400,
        salary_currency="USDOLLARS",
        description="desc\x00ription",
        raw_html_excerpt="<a aria-label=\"이\x00\">",
        extracted_fields={"k\x00": ["v\x00"]},
    )

    jobs._upsert_posting(scan_db, url_row, result, datetime.now(timezone.utc))
    scan_db.commit()

    posting = scan_db.query(JobPosting).filter_by(url_id=url_row.id).one()
    assert len(posting.title) == 255 and len(posting.location) == 255
    assert posting.company_name == "Company"
    assert posting.salary_currency == "USD"
    assert posting.description == "description"
    for stored in (posting.raw_source, posting.extracted_fields, posting.description, posting.title):
        assert not _contains_nul(stored)
    assert "이" in posting.raw_source["html_excerpt"]  # real non-ASCII text is untouched


def test_apply_scan_result_keeps_good_data_on_a_later_failed_rescan(scan_db, make_source, make_url):
    """A re-crawl or admin rescan hitting a transient block (WAF challenge,
    timeout, ...) must not wipe out a posting that already has real data
    from a previous successful scan - same protection rescan_job_url
    already gives the single-URL path.
    """
    url_row = make_url(make_source())
    jobs._apply_scan_result(scan_db, url_row, ScanResult(success=True, title="Real Title", location="Atlanta, GA"))
    scan_db.commit()

    jobs._apply_scan_result(scan_db, url_row, ScanResult(success=False, error="blocked"))
    scan_db.commit()

    posting = scan_db.query(JobPosting).filter_by(url_id=url_row.id).one()
    assert posting.title == "Real Title"
    assert posting.location == "Atlanta, GA"
    assert posting.extraction_status == ScanStatus.SUCCESS  # last-good status, not clobbered
    assert scan_db.get(JobPostingUrl, url_row.id).scan_status == ScanStatus.FAILED  # fresh attempt still recorded


def test_apply_scan_result_marks_a_never_successful_posting_failed(scan_db, make_source, make_url):
    url_row = make_url(make_source())
    _shell_posting(scan_db, url_row)

    jobs._apply_scan_result(scan_db, url_row, ScanResult(success=False, error="blocked"))
    scan_db.commit()

    posting = scan_db.query(JobPosting).filter_by(url_id=url_row.id).one()
    assert posting.extraction_status == ScanStatus.FAILED
    assert posting.title is None


@pytest.fixture
def lane_env(monkeypatch):
    monkeypatch.setattr(jobs, "scan_job_url", lambda url: ScanResult(success=True, title="ok"))
    monkeypatch.setattr(jobs.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(jobs, "enqueue_source_scan", lambda source_id, lanes=1: None)


def _shell_posting(scan_db, url_row) -> None:
    scan_db.add(JobPosting(url_id=url_row.id, extraction_status=ScanStatus.PENDING))
    scan_db.commit()


def test_lane_marks_an_unstorable_result_failed_and_keeps_draining(monkeypatch, scan_db, make_source, make_url, lane_env):
    source = make_source()
    poison = make_url(source, age_minutes=20)
    fine = make_url(source, age_minutes=10)
    _shell_posting(scan_db, poison)
    real_apply = jobs._apply_scan_result

    def apply(db, url_row, result):
        if url_row.id == poison.id:
            raise DataError("UPDATE job_postings ...", {}, Exception("\\u0000 cannot be converted to text.\nDETAIL: ..."))
        return real_apply(db, url_row, result)

    monkeypatch.setattr(jobs, "_apply_scan_result", apply)

    scanned = jobs.run_source_lane(scan_db, source.id)  # must not raise

    assert scanned == 2
    scan_db.expire_all()
    failed = scan_db.get(JobPostingUrl, poison.id)
    assert failed.scan_status == ScanStatus.FAILED
    assert failed.scan_claimed_at is None  # no longer holds one of the source's slots
    assert "DataError" in failed.scan_error and "cannot be converted to text" in failed.scan_error
    assert "UPDATE" not in failed.scan_error  # the statement isn't dumped into the row
    assert scan_db.query(JobPosting).filter_by(url_id=poison.id).one().extraction_status == ScanStatus.FAILED
    assert scan_db.get(JobPostingUrl, fine.id).scan_status == ScanStatus.SUCCESS


def test_lane_lets_a_database_outage_propagate_so_the_message_is_retried(monkeypatch, scan_db, make_source, make_url, lane_env):
    source = make_source()
    row = make_url(source)

    def apply(db, url_row, result):
        raise OperationalError("SELECT 1", {}, Exception("server closed the connection unexpectedly"))

    monkeypatch.setattr(jobs, "_apply_scan_result", apply)

    with pytest.raises(OperationalError):
        jobs.run_source_lane(scan_db, source.id)

    scan_db.expire_all()
    assert scan_db.get(JobPostingUrl, row.id).scan_status == ScanStatus.PENDING  # not blamed on the URL
