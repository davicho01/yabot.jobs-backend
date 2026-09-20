"""How worker.py's job-scan-requests messages are decoded and routed."""

import json
import uuid

import pytest

from app.services import jobs


def _data(payload) -> bytes:
    return json.dumps(payload).encode("utf-8")


def test_parses_a_source_wakeup():
    source_id = uuid.uuid4()
    assert jobs.parse_scan_message(_data({"source_id": str(source_id)})) == ("source", source_id)


def test_parses_the_legacy_per_url_message():
    url_id = uuid.uuid4()
    assert jobs.parse_scan_message(_data({"url_id": str(url_id)})) == ("url", url_id)


@pytest.mark.parametrize(
    "data",
    [
        b"not json",
        b"\xff\xfe",
        _data({}),
        _data({"url_id": "not-a-uuid"}),
        _data({"source_id": None}),
        _data(["url_id"]),
        _data(42),
    ],
)
def test_malformed_messages_raise_value_error(data):
    with pytest.raises(ValueError):
        jobs.parse_scan_message(data)


@pytest.fixture
def routed(monkeypatch):
    calls = {"lane": [], "job": []}
    monkeypatch.setattr(jobs, "run_source_lane", lambda db, source_id: calls["lane"].append(source_id))
    monkeypatch.setattr(jobs, "process_scan_job", lambda db, url_id: calls["job"].append(url_id))
    return calls


def test_source_wakeup_runs_a_lane(scan_db, make_source, routed):
    source = make_source()

    jobs.process_scan_message(scan_db, "source", source.id)

    assert routed == {"lane": [source.id], "job": []}


def test_legacy_message_for_a_crawl_sourced_url_becomes_a_wakeup_for_its_source(scan_db, make_source, make_url, routed):
    source = make_source()
    row = make_url(source)

    jobs.process_scan_message(scan_db, "url", row.id)

    assert routed == {"lane": [source.id], "job": []}


def test_user_submitted_url_is_still_scanned_directly(scan_db, make_url, routed):
    row = make_url(None)

    jobs.process_scan_message(scan_db, "url", row.id)

    assert routed == {"lane": [], "job": [row.id]}


def test_unknown_url_falls_through_to_process_scan_job_which_skips_it(scan_db, routed):
    missing = uuid.uuid4()

    jobs.process_scan_message(scan_db, "url", missing)

    assert routed == {"lane": [], "job": [missing]}


def test_sweep_wakes_each_source_with_pending_urls_once_per_lane(monkeypatch, scan_db, make_source, make_url):
    busy = make_source(max_concurrent_scans=4)
    quiet = make_source()
    make_url(busy)
    make_url(busy)
    make_url(quiet, status="success")
    published = []
    monkeypatch.setattr(jobs, "enqueue_source_scan", lambda source_id, lanes=1: published.append((source_id, lanes)))

    woken = jobs.wake_sources_with_pending_scans(scan_db)

    assert woken == 1
    assert published == [(busy.id, 4)]


def test_sweep_skips_a_source_whose_publish_fails(monkeypatch, scan_db, make_source, make_url):
    first, second = make_source(), make_source()
    make_url(first)
    make_url(second)
    published = []

    def flaky(source_id, lanes=1):
        if source_id == first.id:
            raise RuntimeError("pubsub down")
        published.append(source_id)

    monkeypatch.setattr(jobs, "enqueue_source_scan", flaky)

    woken = jobs.wake_sources_with_pending_scans(scan_db)

    assert woken == 1
    assert published == [second.id]
