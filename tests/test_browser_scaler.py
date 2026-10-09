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

    # Sector classifier: ceil(60 worker * 0.12) = 8, capped at 5.
    assert set_calls == [("yabot-jobs-browser", 8), ("yabot-jobs-sector", 5)]
    assert pause_calls == []


def test_clamps_to_the_min_instances_cap_under_a_big_burst(monkeypatch):
    # (200 + 300) * 0.10 = 50, well past the cap.
    set_calls, _ = _patch(monkeypatch, crawl_worker=200, worker=300)

    browser_scaler.main()

    assert set_calls[0] == ("yabot-jobs-browser", browser_scaler._MIN_INSTANCES_CAP)


def test_scales_down_and_pauses_itself_once_both_are_idle(monkeypatch):
    set_calls, pause_calls = _patch(monkeypatch, crawl_worker=0, worker=0)

    browser_scaler.main()

    assert set_calls == [("yabot-jobs-browser", 0), ("yabot-jobs-sector", 0)]
    assert pause_calls == ["browser-scaler-tick"]


def test_sector_classifier_scales_by_worker_only(monkeypatch):
    # crawl-worker never classifies: ceil(30 worker * 0.12) = 4, whatever crawl-worker does.
    set_calls, _ = _patch(monkeypatch, crawl_worker=10, worker=30)

    browser_scaler.main()

    assert ("yabot-jobs-sector", 4) in set_calls


def test_sector_scaling_failure_never_blocks_browser_scaling_or_the_pause(monkeypatch):
    set_calls, pause_calls = _patch(monkeypatch, crawl_worker=0, worker=0)

    def _set_min_instances(service, count):
        if service == "yabot-jobs-sector":
            raise RuntimeError("403 from Cloud Run Admin")
        set_calls.append((service, count))

    monkeypatch.setattr(browser_scaler, "set_min_instances", _set_min_instances)

    browser_scaler.main()

    assert set_calls == [("yabot-jobs-browser", 0)]
    assert pause_calls == ["browser-scaler-tick"]


def test_stays_active_without_pausing_while_either_service_is_busy(monkeypatch):
    # crawl-worker alone still busy, even with worker idle.
    _, pause_calls = _patch(monkeypatch, crawl_worker=1, worker=0)

    browser_scaler.main()

    assert pause_calls == []
