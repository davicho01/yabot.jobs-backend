from datetime import datetime, timedelta, timezone

from app.models.crawl_source import CrawlSource
from app.services.coverage_monitor import (
    MIN_CRAWLS_FOR_BASELINE,
    SUSTAINED_CRAWLS_TO_FLAG,
    update_coverage,
)


def _source() -> CrawlSource:
    return CrawlSource(name="test", ats_type="greenhouse", board_url="https://boards.greenhouse.io/test")


def test_build_up_phase_never_flags_regardless_of_variance():
    source = _source()
    for count in [100, 1, 200, 2, 150]:  # wildly varying, still within build-up
        update_coverage(source, count)

    assert source.coverage_sample_count == MIN_CRAWLS_FOR_BASELINE
    assert source.coverage_flagged_at is None
    assert source.coverage_low_streak == 0


def test_one_or_two_low_crawls_after_buildup_do_not_flag_and_freeze_baseline():
    source = _source()
    for _ in range(MIN_CRAWLS_FOR_BASELINE):
        update_coverage(source, 100)
    baseline_after_buildup = source.coverage_baseline

    update_coverage(source, 10)  # well below 50% of baseline
    update_coverage(source, 10)

    assert source.coverage_low_streak == 2
    assert source.coverage_flagged_at is None
    assert source.coverage_baseline == baseline_after_buildup  # frozen, not chasing the drop


def test_sustained_low_streak_flags_and_timestamp_does_not_move_forward():
    source = _source()
    for _ in range(MIN_CRAWLS_FOR_BASELINE):
        update_coverage(source, 100)

    for _ in range(SUSTAINED_CRAWLS_TO_FLAG - 1):
        update_coverage(source, 10)
    assert source.coverage_flagged_at is None

    update_coverage(source, 10)  # the Nth consecutive low crawl
    first_flagged_at = source.coverage_flagged_at
    assert first_flagged_at is not None

    later = first_flagged_at + timedelta(hours=2)
    update_coverage(source, 5, now=later)  # still low — flag must not move
    assert source.coverage_flagged_at == first_flagged_at


def test_recovery_clears_flag_resets_streak_and_resumes_baseline_updates():
    source = _source()
    for _ in range(MIN_CRAWLS_FOR_BASELINE):
        update_coverage(source, 100)
    for _ in range(SUSTAINED_CRAWLS_TO_FLAG):
        update_coverage(source, 10)
    assert source.coverage_flagged_at is not None
    frozen_baseline = source.coverage_baseline

    update_coverage(source, 90)  # back above threshold, but not identical to baseline

    assert source.coverage_flagged_at is None
    assert source.coverage_low_streak == 0
    assert source.coverage_baseline != frozen_baseline  # EWMA resumed moving


def test_zero_or_none_baseline_never_divides_by_zero_or_spuriously_flags():
    source = _source()
    for _ in range(MIN_CRAWLS_FOR_BASELINE):
        update_coverage(source, 0)  # baseline settles at 0.0

    assert source.coverage_baseline == 0.0

    for _ in range(SUSTAINED_CRAWLS_TO_FLAG + 1):
        update_coverage(source, 0)  # would be "< 0 * 0.5" if not guarded

    assert source.coverage_flagged_at is None


def test_now_defaults_to_current_time_when_flagging():
    source = _source()
    for _ in range(MIN_CRAWLS_FOR_BASELINE):
        update_coverage(source, 100)
    before = datetime.now(timezone.utc)
    for _ in range(SUSTAINED_CRAWLS_TO_FLAG):
        update_coverage(source, 10)
    after = datetime.now(timezone.utc)

    assert before <= source.coverage_flagged_at <= after
