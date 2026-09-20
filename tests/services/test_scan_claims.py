import uuid

from app.models import JobPostingUrl
from app.models.enums import ScanStatus
from app.services.scan_claims import claim_next_url, has_unclaimed_pending, sources_with_pending_scans

# scan_claim_ttl_seconds defaults to 600: 100s ago is a live claim, 700s ago is abandoned.
_LIVE = 100
_EXPIRED = 700


def test_claims_up_to_the_cap_then_refuses(now, scan_db, make_source, make_url):
    source = make_source(max_concurrent_scans=3)
    for _ in range(5):
        make_url(source)

    claimed = [claim_next_url(scan_db, source.id, now=now) for _ in range(4)]

    assert [c is not None for c in claimed] == [True, True, True, False]
    assert len({c.id for c in claimed if c}) == 3  # three distinct URLs


def test_finishing_a_scan_frees_a_slot(now, scan_db, make_source, make_url):
    source = make_source(max_concurrent_scans=1)
    make_url(source)
    make_url(source)

    first = claim_next_url(scan_db, source.id, now=now)
    assert claim_next_url(scan_db, source.id, now=now) is None  # at cap

    row = scan_db.get(JobPostingUrl, first.id)
    row.scan_status = ScanStatus.SUCCESS
    scan_db.commit()

    assert claim_next_url(scan_db, source.id, now=now) is not None


def test_per_source_cap_is_respected(now, scan_db, make_source, make_url):
    strict = make_source(max_concurrent_scans=1)
    generous = make_source(max_concurrent_scans=5)
    for _ in range(6):
        make_url(strict)
        make_url(generous)

    strict_claims = [claim_next_url(scan_db, strict.id, now=now) for _ in range(6)]
    generous_claims = [claim_next_url(scan_db, generous.id, now=now) for _ in range(6)]

    assert sum(c is not None for c in strict_claims) == 1
    assert sum(c is not None for c in generous_claims) == 5


def test_a_full_source_does_not_block_others(now, scan_db, make_source, make_url):
    busy = make_source(max_concurrent_scans=1)
    idle = make_source(max_concurrent_scans=1)
    make_url(busy, claimed_ago=_LIVE)
    make_url(busy)
    make_url(idle)

    assert claim_next_url(scan_db, busy.id, now=now) is None
    assert claim_next_url(scan_db, idle.id, now=now) is not None


def test_expired_claim_is_reclaimed_and_not_counted_in_flight(now, scan_db, make_source, make_url):
    source = make_source(max_concurrent_scans=1)
    abandoned = make_url(source, claimed_ago=_EXPIRED)

    claimed = claim_next_url(scan_db, source.id, now=now)

    assert claimed is not None and claimed.id == abandoned.id


def test_live_claim_is_not_handed_out_again(now, scan_db, make_source, make_url):
    source = make_source(max_concurrent_scans=5)
    make_url(source, claimed_ago=_LIVE)

    assert claim_next_url(scan_db, source.id, now=now) is None


def test_claims_oldest_first(now, scan_db, make_source, make_url):
    source = make_source(max_concurrent_scans=5)
    newest = make_url(source, age_minutes=1)
    oldest = make_url(source, age_minutes=30)
    middle = make_url(source, age_minutes=10)

    order = [claim_next_url(scan_db, source.id, now=now).id for _ in range(3)]

    assert order == [oldest.id, middle.id, newest.id]


def test_only_pending_rows_are_claimable(now, scan_db, make_source, make_url):
    source = make_source()
    make_url(source, status=ScanStatus.SUCCESS)
    make_url(source, status=ScanStatus.FAILED)

    assert claim_next_url(scan_db, source.id, now=now) is None


def test_unknown_source_claims_nothing(now, scan_db, make_source):
    assert claim_next_url(scan_db, uuid.uuid4(), now=now) is None


def test_claim_persists_the_timestamp(now, scan_db, make_source, make_url):
    source = make_source()
    row = make_url(source)

    claim_next_url(scan_db, source.id, now=now)

    scan_db.refresh(row)
    assert row.scan_claimed_at is not None
    assert row.scan_status == ScanStatus.PENDING  # still pending until the lane stores a result


def test_has_unclaimed_pending(now, scan_db, make_source, make_url):
    source = make_source()
    assert has_unclaimed_pending(scan_db, source.id, now=now) is False

    make_url(source, claimed_ago=_LIVE)
    assert has_unclaimed_pending(scan_db, source.id, now=now) is False  # someone's on it

    make_url(source, claimed_ago=_EXPIRED)
    assert has_unclaimed_pending(scan_db, source.id, now=now) is True  # abandoned


def test_sources_with_pending_scans_reports_lane_count(now, scan_db, make_source, make_url):
    pending = make_source(max_concurrent_scans=4)
    done = make_source()
    make_url(pending)
    make_url(pending)
    make_url(done, status=ScanStatus.SUCCESS)
    make_url(None)  # user-submitted: no source, must not show up

    assert sources_with_pending_scans(scan_db) == [(pending.id, 4)]
