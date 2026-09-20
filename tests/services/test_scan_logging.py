"""INFO logs must actually reach Cloud Logging, and the lane must log enough to
verify the per-source cap from the logs alone."""

import importlib
import logging
import sys

import pytest

from app.core.log_config import configure_logging
from app.models import JobPostingUrl
from app.models.enums import ScanStatus
from app.services import jobs
from app.services.adapters.base import ScanResult
from app.services.scan_claims import claim_next_url


@pytest.fixture
def runtime_preconfigured_root():
    """What the Cloud Functions runtime hands us: a root logger that already
    has a handler and sits at WARNING, so logging.basicConfig() is a no-op —
    the reason no INFO line ever appeared in prod."""
    root = logging.getLogger()
    saved = (root.level, root.handlers[:])
    root.handlers = [logging.NullHandler()]
    root.setLevel(logging.WARNING)
    yield root
    root.handlers, root.level = saved[1], saved[0]


def test_baseline_basicconfig_alone_leaves_info_disabled(runtime_preconfigured_root):
    logging.basicConfig(level=logging.INFO)  # the old behaviour

    assert not logging.getLogger("app.worker").isEnabledFor(logging.INFO)


def test_configure_logging_enables_info_even_when_the_runtime_configured_logging_first(runtime_preconfigured_root):
    configure_logging()

    assert logging.getLogger("app.worker").isEnabledFor(logging.INFO)
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)  # still quiet


@pytest.mark.parametrize("module_name", ["worker", "crawl_worker", "crawl_dispatcher"])
def test_each_function_entry_point_turns_info_logging_on(module_name, runtime_preconfigured_root):
    original = sys.modules.get(module_name)
    sys.modules.pop(module_name, None)
    try:
        importlib.import_module(module_name)  # module-level setup, as on a cold start
        assert logging.getLogger("app." + module_name).isEnabledFor(logging.INFO)
    finally:
        if original is not None:
            sys.modules[module_name] = original
        else:
            sys.modules.pop(module_name, None)


@pytest.fixture
def lane_logs(monkeypatch, caplog):
    monkeypatch.setattr(jobs, "scan_job_url", lambda url: ScanResult(success=True))
    monkeypatch.setattr(jobs.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(jobs, "enqueue_source_scan", lambda source_id, lanes=1: None)
    monkeypatch.setattr(jobs, "_upsert_posting", lambda *args, **kwargs: None)
    caplog.set_level(logging.INFO)
    return caplog


def test_lane_logs_its_start_each_scan_and_its_finish(scan_db, make_source, make_url, lane_logs):
    source = make_source()
    rows = [make_url(source), make_url(source)]

    jobs.run_source_lane(scan_db, source.id)

    messages = [r.getMessage() for r in lane_logs.records]
    assert any(m.startswith("Lane started") and str(source.id) in m for m in messages)
    for row in rows:
        assert any(f"Scanned url_id={row.id} source_id={source.id} success=True in " in m for m in messages)
    assert any("finished after scanning 2 URL(s)" in m for m in messages)


def test_claim_logs_when_a_source_is_at_its_cap(scan_db, make_source, make_url, now, caplog):
    source = make_source(max_concurrent_scans=1)
    make_url(source, claimed_ago=100)  # one scan already in flight
    make_url(source)
    caplog.set_level(logging.INFO)

    assert claim_next_url(scan_db, source.id, now=now) is None

    assert any(
        f"Source {source.id} is at its cap (1/1 scans in flight)" in r.getMessage() for r in caplog.records
    )
    # the row stayed untouched
    assert scan_db.query(JobPostingUrl).filter(JobPostingUrl.scan_status == ScanStatus.PENDING).count() == 2
