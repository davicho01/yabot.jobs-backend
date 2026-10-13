"""Standard titles for a search, from the in-process model
(app.services.standard_titles). Postings get theirs from the sector service
(tests/services/test_job_sector.py).

The parity test needs the model folder (one_off/fetch_standard_title_model.sh)
and is skipped without it: it checks this code gives the same titles as the
training repo's export (parity_cases.json, written by
yabot.jobs-ml standard-title/st_ml/export_onnx.py)."""

import json
from pathlib import Path

import numpy as np
import pytest

from app.core.config import settings
from app.services import standard_titles

TITLES = ["Software Engineer", "Senior Software Engineer", "Backend Engineer", "DevOps Engineer", "CTO"]
MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "standard_title"


def test_answer_always_gives_the_top_title():
    scores = np.array([0.05, 0.1, 0.02, 0.01, 0.0])
    assert standard_titles.answer(scores, TITLES, 0.2, 3) == ["Senior Software Engineer"]


def test_answer_adds_titles_above_the_cutoff_best_first():
    scores = np.array([0.6, 0.9, 0.3, 0.1, 0.0])
    assert standard_titles.answer(scores, TITLES, 0.2, 3) == ["Senior Software Engineer", "Software Engineer", "Backend Engineer"]


def test_answer_caps_at_max_titles():
    scores = np.array([0.9, 0.8, 0.7, 0.6, 0.5])
    assert len(standard_titles.answer(scores, TITLES, 0.2, 3)) == 3


def test_missing_model_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "standard_title_model", str(tmp_path / "nope"))
    assert standard_titles.tag_search("swe") is None


def test_related_adds_each_titles_related_ones_once():
    assert standard_titles.related(["Data Engineer", "Analytics Engineer"]) == [
        "Data Engineer",
        "Analytics Engineer",
        "Data Architect",
        "Data Analyst",
    ]
    assert standard_titles.related([]) == []


def test_list_has_every_title_its_related_titles_name():
    titles = set(standard_titles.list_titles())
    assert len(titles) == 103
    assert set(standard_titles.related(sorted(titles))) == titles


def test_reads_the_model_from_gcs(monkeypatch):
    """A gs:// location reads each file of the folder into memory by name."""
    from google.cloud import storage

    read = []

    class FakeBlob:
        def __init__(self, name):
            self.name = name

        def download_as_bytes(self):
            read.append(self.name)
            return b"{}"

    class FakeClient:
        def bucket(self, name):
            assert name == "yabotjobs-ml-models"
            return type("Bucket", (), {"blob": lambda _self, n: FakeBlob(n)})()

    monkeypatch.setattr(storage, "Client", FakeClient)
    reader = standard_titles._reader("gs://yabotjobs-ml-models/standard-title/v1/")
    assert reader("run.json") == b"{}"
    assert read == ["standard-title/v1/run.json"]


@pytest.mark.skipif(not (MODEL_DIR / "run.json").exists(), reason="model not fetched (one_off/fetch_standard_title_model.sh)")
class TestWithModel:
    @pytest.fixture(autouse=True)
    def model(self, monkeypatch):
        monkeypatch.setattr(settings, "standard_title_model", str(MODEL_DIR))

    def test_list_matches_the_models_titles(self):
        assert standard_titles.list_titles() == json.loads((MODEL_DIR / "run.json").read_text())["titles"]

    def test_a_search_that_isnt_a_tech_title_gets_none(self):
        assert standard_titles.tag_search("barista") == []
        assert standard_titles.tag_search("nurse") == []

    def test_same_titles_as_the_training_export(self):
        cases = [c for c in json.loads((MODEL_DIR / "parity_cases.json").read_text()) if c["kind"] == "search"]
        assert len(cases) == 10
        for case in cases:
            assert standard_titles.tag_search(case["title"]) == case["titles"], case["title"]

    def test_search(self):
        assert standard_titles.tag_search("swe")[0] == "Software Engineer"
        assert standard_titles.tag_search("   ") is None
