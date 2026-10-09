"""app.services.gcp_admin.warm_up_browser_scaler's best-effort behavior —
callers (crawl_dispatcher.py, retry_failed_scans.py) must not have their own
actual work blocked by a Cloud Run Admin / Cloud Scheduler Admin failure.
"""

from app.services import gcp_admin


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
