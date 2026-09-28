"""browser_scaler.py's demand-matching and self-pause logic. All Cloud
Monitoring / Cloud Run Admin / Cloud Scheduler Admin calls are monkeypatched
so no real GCP API call happens.
"""

import browser_scaler


def _patch(monkeypatch, *, crawl_worker: int, worker: int):
    def _get_instance_count(service_name, *, lookback_minutes=5):
        return {"crawl-worker": crawl_worker, "worker": worker}[service_name]

    set_calls = []
    pause_calls = []
    monkeypatch.setattr(browser_scaler, "get_instance_count", _get_instance_count)
    monkeypatch.setattr(browser_scaler, "set_min_instances", lambda service, count: set_calls.append((service, count)))
    monkeypatch.setattr(browser_scaler, "pause_scheduler_job", lambda job: pause_calls.append(job))
    return set_calls, pause_calls


def test_scales_up_proportionally_to_combined_instance_count(monkeypatch):
    # (20 crawl-worker + 60 worker) * 0.10 = 8
    set_calls, pause_calls = _patch(monkeypatch, crawl_worker=20, worker=60)

    browser_scaler.main()

    assert set_calls == [("yabot-jobs-browser", 8)]
    assert pause_calls == []


def test_clamps_to_the_min_instances_cap_under_a_big_burst(monkeypatch):
    # (200 + 300) * 0.10 = 50, well past the cap.
    set_calls, _ = _patch(monkeypatch, crawl_worker=200, worker=300)

    browser_scaler.main()

    assert set_calls == [("yabot-jobs-browser", browser_scaler._MIN_INSTANCES_CAP)]


def test_scales_down_and_pauses_itself_once_both_are_idle(monkeypatch):
    set_calls, pause_calls = _patch(monkeypatch, crawl_worker=0, worker=0)

    browser_scaler.main()

    assert set_calls == [("yabot-jobs-browser", 0)]
    assert pause_calls == ["browser-scaler-tick"]


def test_stays_active_without_pausing_while_either_service_is_busy(monkeypatch):
    # crawl-worker alone still busy, even with worker idle.
    _, pause_calls = _patch(monkeypatch, crawl_worker=1, worker=0)

    browser_scaler.main()

    assert pause_calls == []
