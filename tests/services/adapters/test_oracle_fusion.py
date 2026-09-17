from datetime import datetime, timedelta, timezone

from app.services.adapters import oracle_fusion
from app.services.adapters.base import RECENT_WINDOW_DAYS
from tests.conftest import FakeResponse


def _req(job_id: str, days_ago: int) -> dict:
    day = datetime.now(timezone.utc).date() - timedelta(days=days_ago)
    return {"Id": job_id, "PostedDate": day.isoformat()}


def test_fetch_jobs_keeps_postings_within_window_and_stops_at_boundary(monkeypatch):
    requisitions = [
        _req("1", 0),
        _req("2", RECENT_WINDOW_DAYS - 1),
        _req("3", RECENT_WINDOW_DAYS),
    ]
    calls = []

    def fake_get(url, params, **kwargs):
        calls.append(params["finder"])
        return FakeResponse(json_data={"items": [{"requisitionList": requisitions}]})

    monkeypatch.setattr(oracle_fusion, "get_with_retry", fake_get)
    urls = oracle_fusion._fetch_jobs("example.oraclecloud.com/CX_1")
    assert urls == [
        oracle_fusion._ORACLE_FUSION_JOB_URL.format(host="example.oraclecloud.com", site_number="CX_1", job_id="1"),
        oracle_fusion._ORACLE_FUSION_JOB_URL.format(host="example.oraclecloud.com", site_number="CX_1", job_id="2"),
    ]
    assert len(calls) == 1  # stopped after the first page
