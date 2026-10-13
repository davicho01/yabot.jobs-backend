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


def _page(meta=None, og=None) -> str:
    tags = (f'<meta name="description" content="{meta}">' if meta else "") + (
        f'<meta property="og:description" content="{og}">' if og else ""
    )
    return f"<html><head>{tags}</head><body></body></html>"


OG_TEASER = "Location: Midvale, UT. You report to the Software Engineering Manager. " * 4 + "Our Benefits: Comp"  # 305


def test_page_teaser_over_the_threshold_is_incomplete():
    for html in (_page(og=OG_TEASER), _page(meta=OG_TEASER)):
        result = ScanResult(success=True, title="Engineer", description=OG_TEASER, full_html=html)
        assert incomplete_reasons(result) == ["description is only the page's meta/og description"]


def test_page_teaser_matches_ignoring_whitespace():
    reflowed = OG_TEASER.replace(". ", ".\n\n")
    result = ScanResult(success=True, title="Engineer", description=reflowed, raw_html_excerpt=_page(og=OG_TEASER))
    assert incomplete_reasons(result) == ["description is only the page's meta/og description"]


def test_description_longer_than_the_pages_summary_is_complete():
    longer = OG_TEASER + " " + FULL
    result = ScanResult(success=True, title="Engineer", description=longer, full_html=_page(og=OG_TEASER))
    assert incomplete_reasons(result) == []


def test_long_meta_description_holding_the_whole_posting_is_complete():
    posting = "We are hiring. " * 70  # 1,050 characters
    result = ScanResult(success=True, title="Engineer", description=posting, full_html=_page(meta=posting))
    assert incomplete_reasons(result) == []


def test_threshold_is_configurable(monkeypatch):
    monkeypatch.setattr(settings, "scan_min_description_chars", 50)
    assert incomplete_reasons(ScanResult(success=True, title="Engineer", description=TEASER)) == []


# ------------------------------------------------------ storing the result


def _posting(scan_db, url_row) -> JobPosting:
    return scan_db.query(JobPosting).filter_by(url_id=url_row.id).one()


def _searchable_ids(scan_db) -> set:
    stmt, _order, _area = jobs.build_job_search_statement()
    return {row.id for row in scan_db.scalars(stmt)}


def test_incomplete_scan_is_stored_hidden_and_retried(scan_db, make_source, make_url):
    url_row = make_url(make_source())

    jobs._apply_scan_result(scan_db, url_row, _incomplete(location="Taguig"))
    scan_db.commit()

    posting = _posting(scan_db, url_row)
    assert posting.description == TEASER and posting.location == "Taguig"  # stored
    assert posting.extraction_status == ScanStatus.FAILED
    assert url_row.id not in _searchable_ids(scan_db)  # hidden
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
    assert _posting(scan_db, url_row).extraction_status == ScanStatus.NEEDS_REVIEW  # still hidden


def test_incomplete_rescan_keeps_an_earlier_complete_posting_searchable(scan_db, make_source, make_url):
    url_row = make_url(make_source())
    jobs._apply_scan_result(scan_db, url_row, _complete(location="Atlanta, GA"))
    scan_db.commit()

    jobs._apply_scan_result(scan_db, url_row, _incomplete())
    scan_db.commit()

    posting = _posting(scan_db, url_row)
    assert posting.description == FULL and posting.location == "Atlanta, GA"
    assert posting.extraction_status == ScanStatus.SUCCESS
    assert url_row.id in _searchable_ids(scan_db)
    assert scan_db.get(JobPostingUrl, url_row.id).scan_status == ScanStatus.FAILED  # still retried


def test_complete_rescan_makes_it_searchable_again(scan_db, make_source, make_url):
    url_row = make_url(make_source())
    jobs._apply_scan_result(scan_db, url_row, _incomplete())
    scan_db.commit()

    jobs._apply_scan_result(scan_db, url_row, _complete())
    scan_db.commit()

    url_row = scan_db.get(JobPostingUrl, url_row.id)
    assert url_row.scan_status == ScanStatus.SUCCESS
    assert url_row.scan_attempts == 0 and url_row.next_retry_at is None and url_row.scan_error is None
    assert _posting(scan_db, url_row).description == FULL
    assert url_row.id in _searchable_ids(scan_db)


# ------------------------------------------------------------ duplicates


# Same setup as test_source_company_names.TestOfficialVsElsewhere: copies of
# one job need the same company and a metro-level location to dedup, and a
# URL with no crawl source keeps the page's company name.


def _scan_copy(scan_db, url_row, description):
    result = ScanResult(
        success=True, title="Engineer", company_name="Acme", location="Austin, TX", description=description
    )
    result.incomplete_reasons = incomplete_reasons(result)
    jobs._apply_scan_result(scan_db, url_row, result)
    scan_db.commit()
    return _posting(scan_db, url_row)


def _job_board(scan_db, make_source):
    board = make_source()
    board.name, board.status, board.is_official = "linkedin.com", "pending", False
    scan_db.commit()
    return board


def test_control_two_complete_copies_link(scan_db, make_url):
    # Guards the two tests below: this pair, both complete, does dedup — so
    # their results aren't just a scenario that never matches.
    first = _scan_copy(scan_db, make_url(None), FULL)
    second = _scan_copy(scan_db, make_url(None), FULL)

    assert second.primary_posting_id == first.id


def test_complete_copy_never_attaches_to_a_hidden_incomplete_one(scan_db, make_url):
    _scan_copy(scan_db, make_url(None), TEASER)
    complete_row = make_url(None)
    complete = _scan_copy(scan_db, complete_row, FULL)

    assert complete.primary_posting_id is None
    assert complete_row.id in _searchable_ids(scan_db)


def test_incomplete_official_copy_does_not_take_over_a_complete_one(scan_db, make_source, make_url):
    acme = make_source()
    acme.name = "Acme"
    scan_db.commit()
    board_row = make_url(_job_board(scan_db, make_source))
    board_copy = _scan_copy(scan_db, board_row, FULL)

    official = _scan_copy(scan_db, make_url(acme), TEASER)
    scan_db.expire_all()

    assert scan_db.get(JobPosting, board_copy.id).primary_posting_id is None
    assert board_row.id in _searchable_ids(scan_db)
    assert scan_db.get(JobPosting, official.id).extraction_status == ScanStatus.FAILED


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
