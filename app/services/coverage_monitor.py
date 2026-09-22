from datetime import datetime, timezone

from app.models.crawl_source import CrawlSource

# Generic, adapter-agnostic anomaly detection for CrawlSource.fetch_jobs
# discovery counts — catches a source's coverage silently regressing (a
# broken adapter, an ATS API/rate-limit change, a board going empty, ...)
# for any platform, without any per-adapter opt-in. It has nothing to
# regress *from* on a source's very first crawls, so it can't catch a source
# that's been under-counting since day one — that class of bug is prevented
# at the adapter level instead (see app.services.adapters.base module docs).

# How many crawls build the initial baseline before flagging can trigger —
# a brand-new source has no "normal" to compare against yet.
MIN_CRAWLS_FOR_BASELINE = 5
# Flag when a crawl's discovered count falls below this fraction of baseline.
COVERAGE_DROP_THRESHOLD = 0.5
# Require this many consecutive low crawls before flagging — a single blip
# (transient rate-limiting, a momentary API hiccup) shouldn't trip it.
SUSTAINED_CRAWLS_TO_FLAG = 3
# Smoothing factor for the running baseline once past the build-up phase.
EWMA_ALPHA = 0.3


def update_coverage(source: CrawlSource, discovered_count: int, now: datetime | None = None) -> None:
    """Record this crawl's discovered-URL count and update the source's
    coverage-drop flag in place. Call only on a crawl's success path — a
    failed crawl's "0 discovered" would corrupt the baseline, and is already
    a distinct, handled concern (CrawlSource.last_error)."""
    now = now or datetime.now(timezone.utc)
    source.coverage_last_count = discovered_count
    # ORM column defaults only apply on flush, not on a bare, unpersisted
    # CrawlSource() — normalize here rather than relying on that timing.
    sample_count = source.coverage_sample_count or 0
    low_streak = source.coverage_low_streak or 0

    if sample_count < MIN_CRAWLS_FOR_BASELINE:
        prior = source.coverage_baseline or 0.0
        source.coverage_baseline = prior + (discovered_count - prior) / (sample_count + 1)
        source.coverage_sample_count = sample_count + 1
        source.coverage_low_streak = 0
        source.coverage_flagged_at = None
        return

    baseline = source.coverage_baseline or 0.0
    is_low = baseline > 0 and discovered_count < baseline * COVERAGE_DROP_THRESHOLD
    source.coverage_sample_count = sample_count + 1

    if is_low:
        source.coverage_low_streak = low_streak + 1
        if source.coverage_low_streak >= SUSTAINED_CRAWLS_TO_FLAG and source.coverage_flagged_at is None:
            source.coverage_flagged_at = now
        # Baseline stays frozen on every low reading (not only once actually
        # flagged) — otherwise it would drift toward the anomaly during the
        # build-up to SUSTAINED_CRAWLS_TO_FLAG, making "sustained" pointless.
    else:
        source.coverage_low_streak = 0
        source.coverage_flagged_at = None  # auto-clears once coverage recovers, no manual dismiss needed
        source.coverage_baseline = baseline + EWMA_ALPHA * (discovered_count - baseline)
