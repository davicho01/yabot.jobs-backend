"""Thin REST clients for the handful of Google Cloud control-plane APIs
crawl_dispatcher.py and browser_scaler.py need to scale yabot-jobs-browser
to match real crawl-worker/worker demand: Cloud Monitoring (how many
instances are actually running), Cloud Run Admin (how many to keep warm),
and Cloud Scheduler Admin (turn browser_scaler.py's own tick on/off).

Plain httpx + an OAuth access token from ambient credentials, same
lightweight-REST style as browser_fetch.py's ID-token client, rather than
pulling in the heavier google-cloud-run/-monitoring/-scheduler SDKs for a
handful of one-off calls.

Reads GCP_PROJECT_ID straight from the environment rather than importing
app.core.config.settings: browser_scaler.py — the whole reason this module
exists — deliberately isn't granted DATABASE_URL/API keys/any of the other
secrets Settings() requires (it never touches the database or an LLM), so
importing the full settings singleton here would crash it at startup. Read
lazily (a function, not a module-level constant) for the same reason
crawl_queue.py builds its Pub/Sub clients lazily: reading os.environ at
import time would blow up `import browser_scaler` itself — including for
anything that merely imports this module in a test — before a caller ever
gets the chance to set the var or monkeypatch anything.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
from google.auth import default as google_auth_default
from google.auth.transport.requests import Request as GoogleAuthRequest

logger = logging.getLogger("app.gcp_admin")


def _project_id() -> str:
    return os.environ["GCP_PROJECT_ID"]


# Every service this module touches lives here — same region every other
# Cloud Run resource in deploy/gcloud-deploy.sh uses.
_REGION = "us-central1"

_credentials = None


def _access_token() -> str:
    # Cached across calls within one process (a Cloud Function invocation),
    # refreshed automatically once expired — same ambient-credentials
    # pattern as browser_fetch.py's ID token, just an OAuth access token
    # instead (these control-plane APIs check IAM permissions, not a
    # per-service invoker binding).
    global _credentials
    if _credentials is None:
        _credentials, _ = google_auth_default()
    _credentials.refresh(GoogleAuthRequest())
    return _credentials.token


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_access_token()}"}


def get_instance_count(service_name: str, *, lookback_minutes: int = 5) -> int:
    """Max concurrent Cloud Run instance count for `service_name` over the
    last `lookback_minutes` — deliberately a window, not an instant point,
    so a momentary dip to 0 between scan-lane wake-ups doesn't read as
    "idle" (see browser_scaler.py). Returns 0 if the metric has no data
    (nothing has run recently).

    Counts only state="active" instances: Cloud Run keeps an idle instance
    around for up to ~15 minutes after its last request (not configurable),
    and counting those held yabot-jobs-browser's warm floor up long after a
    burst drained. Verified live 2026-10-06: after the 2pm burst, worker had
    0-3 active vs 9-17 idle instances for ~15 minutes."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=lookback_minutes)
    response = httpx.get(
        f"https://monitoring.googleapis.com/v3/projects/{_project_id()}/timeSeries",
        headers=_headers(),
        params={
            "filter": (
                'metric.type="run.googleapis.com/container/instance_count" '
                f'AND resource.labels.service_name="{service_name}" '
                'AND metric.labels.state="active"'
            ),
            "interval.startTime": start.isoformat(),
            "interval.endTime": now.isoformat(),
            "aggregation.alignmentPeriod": f"{lookback_minutes * 60}s",
            "aggregation.perSeriesAligner": "ALIGN_MAX",
            "aggregation.crossSeriesReducer": "REDUCE_SUM",
            "aggregation.groupByFields": "resource.labels.service_name",
        },
        timeout=30.0,
    )
    response.raise_for_status()
    series = response.json().get("timeSeries", [])
    if not series or not series[0].get("points"):
        return 0
    return int(series[0]["points"][0]["value"]["int64Value"])


def get_min_instances(service_name: str) -> int:
    response = httpx.get(
        f"https://run.googleapis.com/v2/projects/{_project_id()}/locations/{_REGION}/services/{service_name}",
        headers=_headers(),
        timeout=30.0,
    )
    response.raise_for_status()
    return int(response.json().get("template", {}).get("scaling", {}).get("minInstanceCount", 0))


def set_min_instances(service_name: str, count: int) -> bool:
    """PATCH `service_name`'s min-instances to `count`, skipping the call
    (and the new revision it would create) if it's already there. Returns
    whether it actually changed anything."""
    if get_min_instances(service_name) == count:
        return False
    response = httpx.patch(
        f"https://run.googleapis.com/v2/projects/{_project_id()}/locations/{_REGION}/services/{service_name}",
        headers=_headers(),
        params={"updateMask": "template.scaling.minInstanceCount"},
        json={"template": {"scaling": {"minInstanceCount": count}}},
        timeout=30.0,
    )
    response.raise_for_status()
    logger.info("Set %s min-instances to %d.", service_name, count)
    return True


def _set_scheduler_job_paused(job_name: str, *, paused: bool) -> None:
    action = "pause" if paused else "resume"
    response = httpx.post(
        f"https://cloudscheduler.googleapis.com/v1/projects/{_project_id()}/locations/{_REGION}/jobs/{job_name}:{action}",
        headers=_headers(),
        timeout=30.0,
    )
    response.raise_for_status()
    logger.info("%sd Cloud Scheduler job %s.", action.capitalize(), job_name)


def pause_scheduler_job(job_name: str) -> None:
    _set_scheduler_job_paused(job_name, paused=True)


def resume_scheduler_job(job_name: str) -> None:
    # Resuming an already-ENABLED job is a documented no-op, so callers
    # don't need to check current state first.
    _set_scheduler_job_paused(job_name, paused=False)


_BROWSER_SERVICE = "yabot-jobs-browser"
_BROWSER_SCALER_SCHEDULER_JOB = "browser-scaler-tick"
_BROWSER_WARMUP_MIN_INSTANCES = 3
# yabot-jobs-sector (the sector classifier) is warmed at the same moment: every
# new posting a scan saves calls it, and browser_scaler.py's first tick still
# sees 0 workers (5-minute lookback) — verified live 2026-10-09, the first
# scan after it shipped timed out twice in the first two minutes.
_SECTOR_SERVICE = "yabot-jobs-sector"
_SECTOR_WARMUP_MIN_INSTANCES = 4


def warm_up_browser_scaler() -> None:
    """Warm up yabot-jobs-browser (and yabot-jobs-sector) immediately and turn
    browser_scaler.py's tick back on, for any caller whose own work might need a browser render
    (see app.services.browser_fetch) — crawl_dispatcher.py's 3x/day and
    manually-triggered runs, and retry_failed_scans.py's hourly sweep, both
    call this at the top of their own main(). Without it, a scan that falls
    between crawl_dispatcher.py runs hits a cold (min-instances=0) browser
    with nothing warming it back up — verified live 2026-09-28: an IBM scan
    retry failed hitting exactly that gap. browser_scaler.py itself pauses
    the tick again once crawl-worker/worker go quiet; see that module's own
    docstring. Best-effort: a failure here shouldn't block the caller's own
    actual work.
    """
    try:
        set_min_instances(_BROWSER_SERVICE, _BROWSER_WARMUP_MIN_INSTANCES)
        resume_scheduler_job(_BROWSER_SCALER_SCHEDULER_JOB)
    except Exception:
        logger.exception("Failed to warm up %s / resume the scaler tick.", _BROWSER_SERVICE)
    try:  # separately, so a classifier problem never touches the browser warm-up above
        set_min_instances(_SECTOR_SERVICE, _SECTOR_WARMUP_MIN_INSTANCES)
    except Exception:
        logger.exception("Failed to warm up %s.", _SECTOR_SERVICE)
