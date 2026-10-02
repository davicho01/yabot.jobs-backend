"""Matches yabot-jobs-browser's warm capacity to real crawl-worker/worker
demand, instead of a flat always-on guess.

Only ~14% of active CrawlSources (ashby/ultipro/taleo/bestbuy/avature/
talemetry) unconditionally need a browser render; the rest hit it
reactively via app.services.adapters.base.fetch_html's fallback. Demand is
bursty around crawl_dispatcher.py's hourly runs, not constant, so rather
than pay for warm Chromium instances 24/7 this ticks every 2 minutes (see
deploy/gcloud-deploy.sh's browser-scaler-tick Cloud Scheduler job) *only*
while crawl_dispatcher.py has turned that ticking on (it resumes the
Scheduler job at the top of its own main(), immediately after a scheduled
or manually-triggered run starts). This module is the one that turns it
back off, in main() below, once it observes both crawl-worker and worker
have been idle for a full lookback window — crawl_dispatcher.py itself
finishing is not the same signal, since crawl-worker/worker keep working
through a queued backlog well after dispatch has exited.

Verified live 2026-09-28: yabot-jobs-browser was failing ~69% of requests
with 429s because it was stuck at a static min-instances that had been
manually reset to 0, unrelated to how much crawl-worker/worker traffic
actually existed at the time.

Usage: python browser_scaler.py
"""

import logging

from app.core.log_config import configure_logging
from app.services.gcp_admin import get_instance_count, pause_scheduler_job, set_min_instances

configure_logging()
logger = logging.getLogger("app.browser_scaler")

_BROWSER_SERVICE = "yabot-jobs-browser"
_SCALER_SCHEDULER_JOB = "browser-scaler-tick"

# Started at 0.15 (399 of 2,807 active sources, 14.2%, use an ATS type
# that unconditionally needs a browser render). Tuned down to 0.10 after
# the first live runs: observed cost per cycle (~$2.50, mostly
# yabot-jobs-browser's own warm-instance time) was worth trimming, and the
# 429 rate has headroom to absorb a lower floor before it'd matter — still
# not a derived constant, re-tune either direction against real 429/cost
# numbers as they come in.
_BROWSER_DEMAND_RATIO = 0.10

# Cost safety rail, not the real ceiling: yabot-jobs-browser's own
# max-instances=100 still autoscales reactively on top of whatever floor
# this sets, so under-shooting this cap costs cold-start lag, not an
# outage.
_MIN_INSTANCES_CAP = 30

# How far back get_instance_count looks — deliberately a window, not an
# instant point, so a momentary dip to 0 between scan-lane wake-ups isn't
# mistaken for "fully drained" and ends the window early.
_LOOKBACK_MINUTES = 5


def main() -> None:
    crawl_worker_count = get_instance_count("crawl-worker", lookback_minutes=_LOOKBACK_MINUTES)
    worker_count = get_instance_count("worker", lookback_minutes=_LOOKBACK_MINUTES)
    total = crawl_worker_count + worker_count
    desired = min(round(_BROWSER_DEMAND_RATIO * total), _MIN_INSTANCES_CAP)
    logger.info(
        "crawl-worker=%d worker=%d (last %dm) -> desired min-instances=%d",
        crawl_worker_count,
        worker_count,
        _LOOKBACK_MINUTES,
        desired,
    )

    set_min_instances(_BROWSER_SERVICE, desired)

    if total == 0:
        # Both services have been quiet for the full lookback window: the
        # pipeline has fully drained (dispatch done, crawl-worker done
        # discovering, scan lanes done processing). Nothing left to watch
        # for until crawl_dispatcher.py's next run resumes this job.
        pause_scheduler_job(_SCALER_SCHEDULER_JOB)


def dispatch(_request=None) -> tuple[str, int]:
    """Cloud Functions (2nd gen) HTTP entry point — Cloud Scheduler calls
    this every 2 minutes while browser-scaler-tick is resumed."""
    main()
    return "ok", 200


if __name__ == "__main__":
    main()
