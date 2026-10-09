from datetime import datetime, timedelta, timezone

from app.models import JobPostingUrl
from app.models.enums import ScanStatus
from app.services import jobs


def test_the_retry_sweep_skips_closed_jobs(scan_db, make_source, make_url):
    # A job its own page reports closed (or that left its board) is FAILED
    # with a retry time like any failure; re-fetching it would only spend
    # browser renders on a listing nobody sees.
    source = make_source()
    due = datetime.now(timezone.utc) - timedelta(minutes=1)
    open_row = make_url(source, status=ScanStatus.FAILED)
    closed_row = make_url(source, status=ScanStatus.FAILED)
    for row in (open_row, closed_row):
        row.next_retry_at = due
    closed_row.closed_at = due
    scan_db.commit()

    assert jobs.wake_retryable_failed_scans(scan_db) == 1
    scan_db.expire_all()

    assert scan_db.get(JobPostingUrl, open_row.id).scan_status == ScanStatus.PENDING
    assert scan_db.get(JobPostingUrl, closed_row.id).scan_status == ScanStatus.FAILED
