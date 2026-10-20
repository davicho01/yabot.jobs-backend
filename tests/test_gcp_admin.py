"""app.services.gcp_admin.warm_up_browser_scaler's best-effort behavior —
callers (crawl_dispatcher.py, retry_failed_scans.py) must not have their own
actual work blocked by a Cloud Run Admin / Cloud Scheduler Admin failure.
"""

from datetime import datetime, timezone

from app.services import gcp_admin
from tests.conftest import FakeResponse


def test_warms_the_browser_and_resumes_the_scaler_tick(monkeypatch):
    calls = []
    monkeypatch.setattr(
        gcp_admin, "set_min_instances", lambda service, count: calls.append(("set", service, count))
    )
    monkeypatch.setattr(gcp_admin, "resume_scheduler_job", lambda job: calls.append(("resume", job)))

    gcp_admin.warm_up_browser_scaler()

    assert calls == [
        ("set", "yabot-jobs-browser", 3),
        ("resume", "browser-scaler-tick"),
        ("set", "yabot-jobs-sector", 4),
    ]


def test_sector_warmup_failure_never_blocks_the_browser_or_the_tick(monkeypatch):
    calls = []

    def _set(service, count):
        if service == "yabot-jobs-sector":
            raise RuntimeError("403 from Cloud Run Admin")
        calls.append(("set", service, count))

    monkeypatch.setattr(gcp_admin, "set_min_instances", _set)
    monkeypatch.setattr(gcp_admin, "resume_scheduler_job", lambda job: calls.append(("resume", job)))

    gcp_admin.warm_up_browser_scaler()  # must not raise

    assert calls == [("set", "yabot-jobs-browser", 3), ("resume", "browser-scaler-tick")]


def test_does_not_raise_if_set_min_instances_fails(monkeypatch):
    def _boom(service, count):
        raise RuntimeError("Cloud Run Admin API unavailable")

    monkeypatch.setattr(gcp_admin, "set_min_instances", _boom)
    resume_calls = []
    monkeypatch.setattr(gcp_admin, "resume_scheduler_job", lambda job: resume_calls.append(job))

    gcp_admin.warm_up_browser_scaler()  # must not raise

    # The scheduler resume never even gets attempted once the warmup blows up.
    assert resume_calls == []


def test_does_not_raise_if_resume_scheduler_job_fails(monkeypatch):
    monkeypatch.setattr(gcp_admin, "set_min_instances", lambda service, count: None)

    def _boom(job):
        raise RuntimeError("Cloud Scheduler Admin API unavailable")

    monkeypatch.setattr(gcp_admin, "resume_scheduler_job", _boom)

    gcp_admin.warm_up_browser_scaler()  # must not raise


def _fake_run_admin(monkeypatch, service_json):
    patches = []
    monkeypatch.setenv("GCP_PROJECT_ID", "yabotjobs")
    monkeypatch.setattr(gcp_admin, "_headers", lambda: {})
    monkeypatch.setattr(gcp_admin.httpx, "get", lambda *a, **k: FakeResponse(json_data=service_json))
    monkeypatch.setattr(
        gcp_admin.httpx, "patch", lambda url, **k: patches.append((url, k["params"], k["json"])) or FakeResponse()
    )
    return patches


def test_sets_the_service_level_min_not_the_revision_template(monkeypatch):
    # A template change would create a new revision and cold-start its floor.
    patches = _fake_run_admin(monkeypatch, {"scaling": {"minInstanceCount": 2}, "template": {"scaling": {}}})

    assert gcp_admin.set_min_instances("yabot-jobs-sector", 5) is True

    assert patches == [(
        "https://run.googleapis.com/v2/projects/yabotjobs/locations/us-central1/services/yabot-jobs-sector",
        {"updateMask": "scaling.minInstanceCount"},
        {"scaling": {"minInstanceCount": 5}},
    )]


def test_skips_the_patch_when_the_service_level_min_already_matches(monkeypatch):
    patches = _fake_run_admin(monkeypatch, {"scaling": {"minInstanceCount": 5}, "template": {"scaling": {}}})

    assert gcp_admin.set_min_instances("yabot-jobs-sector", 5) is False
    assert patches == []


def test_scheduler_job_updated_at_reads_user_update_time(monkeypatch):
    # Real shape from prod (2026-10-20); the API can return up to 9 fractional digits.
    _fake_run_admin(monkeypatch, {"state": "PAUSED", "userUpdateTime": "2026-10-20T03:02:35.924393123Z"})

    assert gcp_admin.scheduler_job_updated_at("browser-scaler-tick") == datetime(
        2026, 10, 20, 3, 2, 35, tzinfo=timezone.utc
    )
