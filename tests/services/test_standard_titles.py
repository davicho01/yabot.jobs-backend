"""Standard titles from the in-process model (app.services.standard_titles).

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


@pytest.fixture
def fresh_model(monkeypatch):
    """Forget any loaded model so each test loads from its own folder."""
    monkeypatch.setattr(standard_titles, "_model", None)
    monkeypatch.setattr(standard_titles, "_load_failed", False)


def test_answer_always_gives_the_top_title():
    scores = np.array([0.05, 0.1, 0.02, 0.01, 0.0])
    assert standard_titles.answer(scores, TITLES, 0.2, 3) == ["Senior Software Engineer"]


def test_answer_adds_titles_above_the_cutoff_best_first():
    scores = np.array([0.6, 0.9, 0.3, 0.1, 0.0])
    assert standard_titles.answer(scores, TITLES, 0.2, 3) == ["Senior Software Engineer", "Software Engineer", "Backend Engineer"]


def test_answer_caps_at_max_titles():
    scores = np.array([0.9, 0.8, 0.7, 0.6, 0.5])
    assert len(standard_titles.answer(scores, TITLES, 0.2, 3)) == 3


def test_clean_matches_training():
    assert standard_titles._clean("<p>Build&nbsp;APIs</p>\n\n<ul><li>Go</li></ul>") == "Build APIs Go"
    assert standard_titles._clean(None) == ""
    assert standard_titles._clean("x" * 10, 4) == "xxxx"


def test_missing_model_returns_none(fresh_model, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "standard_title_model_dir", str(tmp_path / "nope"))
    assert standard_titles.tag_posting("Software Engineer", "Build things.") is None
    assert standard_titles.tag_search("swe") is None
    assert standard_titles.model_version() is None


@pytest.mark.skipif(not (MODEL_DIR / "run.json").exists(), reason="model not fetched (one_off/fetch_standard_title_model.sh)")
class TestWithModel:
    @pytest.fixture(autouse=True)
    def model(self, fresh_model, monkeypatch):
        monkeypatch.setattr(settings, "standard_title_model_dir", str(MODEL_DIR))

    def test_same_titles_as_the_training_export(self):
        cases = json.loads((MODEL_DIR / "parity_cases.json").read_text())
        for case in cases:
            if case["kind"] == "posting":
                got = standard_titles.tag_posting(case["title"], case["description"])
            else:
                got = standard_titles.tag_search(case["title"])
            assert got == case["titles"], case["title"]

    def test_search(self):
        assert standard_titles.tag_search("swe")[0] == "Software Engineer"
        assert standard_titles.tag_search("   ") is None

    def test_posting_gets_one_to_three_titles(self):
        titles = standard_titles.tag_posting("Senior Site Reliability Engineer", "<p>Run our Kubernetes platform.</p>")
        assert 1 <= len(titles) <= 3
        assert titles[0] == "Senior Site Reliability Engineer"

    def test_version(self):
        assert standard_titles.model_version() == json.loads((MODEL_DIR / "run.json").read_text())["version"]
