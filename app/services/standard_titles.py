"""Standard job titles for a typed search, from our own model.

Every Tech posting has 1-3 titles from a fixed list of 103 standard tech
titles ("Senior Software Engineer", "DevOps Engineer", ...) — set by the
sector service in the same call as its sector (see app.services.job_sector).
A typed search is translated here into the same titles ("swe" -> Software
Engineer), and title search then also matches postings tagged with those
titles or their related ones (app.services.jobs.build_job_search_statement).

The model (Ettin 32M, trained in yabot.jobs-ml/standard-title — the same one
the sector service runs) runs here, in process, for the API and saved-search
alerts. Each process reads it into memory on first use from
settings.standard_title_model — a gs:// folder on prod, a local folder in dev
and tests. The folder holds model.onnx, tokenizer.json and run.json (the
title list in output order, the cutoff, max lengths and the version).

A search is the typed text, whitespace collapsed, cut at 64 tokens — as in
training (yabot.jobs-ml standard-title/st_ml/predict.py). Answer: the top
title always, plus others scoring at least the cutoff, at most 3. A search
whose top title scores below the cutoff ("barista", "nurse") isn't a tech
title at all and gets no titles, so it stays a literal title match.

Never raises: a missing model or any failure returns None and logs, like
app.services.sector_api. Callers treat None as "literal title match only".
"""

import csv
import functools
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.core.config import settings

logger = logging.getLogger("app.standard_titles")

# The approved list (yabot.jobs-ml standard-title/data/standard_titles.csv):
# standard_title | related_titles. Copy it here when the list changes.
_LIST_PATH = Path(__file__).resolve().parents[1] / "data" / "standard_titles.csv"


@dataclass(frozen=True)
class _Model:
    session: object  # onnxruntime.InferenceSession
    tokenizer: object  # tokenizers.Tokenizer, truncating at the search max length (64)
    titles: list[str]
    cutoff: float
    max_titles: int
    version: str


_model: _Model | None = None
_load_failed = False
_lock = threading.Lock()


def _normalize(text: str | None) -> str:
    return " ".join(str(text or "").split())


def _reader(location: str) -> Callable[[str], bytes]:
    """Reads a file of the model folder by name, from gs://bucket/prefix or a local folder."""
    if location.startswith("gs://"):
        from google.cloud import storage

        bucket_name, _, prefix = location.removeprefix("gs://").partition("/")
        bucket = storage.Client().bucket(bucket_name)
        return lambda name: bucket.blob(f"{prefix.strip('/')}/{name}").download_as_bytes()
    folder = Path(location)
    return lambda name: (folder / name).read_bytes()


def _load() -> _Model | None:
    """The model, loaded once per process. None if it isn't available."""
    global _model, _load_failed
    if _model is not None or _load_failed:
        return _model
    with _lock:
        if _model is not None or _load_failed:
            return _model
        location = settings.standard_title_model
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            read = _reader(location)
            run = json.loads(read("run.json"))
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1  # a search is a few ms; keep the API's other requests their CPU
            session = ort.InferenceSession(read(run["file"]), options, providers=["CPUExecutionProvider"])
            tokenizer = Tokenizer.from_str(read("tokenizer.json").decode())
            tokenizer.enable_truncation(int(run["search_max_length"]))
            _model = _Model(
                session=session,
                tokenizer=tokenizer,
                titles=run["titles"],
                cutoff=float(run["cutoff"]),
                max_titles=int(run["max_titles"]),
                version=run["version"],
            )
            logger.info("Loaded standard-title model %s from %s", _model.version, location)
        except Exception:
            _load_failed = True  # don't retry the load on every search; a new process (deploy) tries again
            logger.warning("Standard-title model not available at %s; searches are literal only.", location, exc_info=True)
        return _model


def answer(scores: np.ndarray, titles: list[str], cutoff: float, max_titles: int) -> list[str]:
    """The top title always, then others scoring at least the cutoff, at most max_titles in all."""
    order = np.argsort(-scores)[:max_titles]
    return [titles[order[0]]] + [titles[i] for i in order[1:] if scores[i] >= cutoff]


def tag_search(text: str | None) -> list[str] | None:
    """1-3 standard titles for a typed search; [] when it isn't a tech title
    (top score below the cutoff); None if the model isn't available."""
    if _load() is None or not _normalize(text):
        return None
    titles = _search_titles(_normalize(text))
    return None if titles is None else list(titles)


@functools.lru_cache(maxsize=10_000)
def _search_titles(text: str) -> tuple[str, ...] | None:
    """tag_search for normalized text; repeat searches skip the model (a few ms each)."""
    model = _load()
    try:
        ids = np.array([model.tokenizer.encode(text).ids], dtype=np.int64)
        logits = model.session.run(None, {"input_ids": ids, "attention_mask": np.ones_like(ids)})[0][0]
    except Exception:
        logger.warning("Standard-title model failed on a search; literal match only.", exc_info=True)
        return None
    scores = 1 / (1 + np.exp(-logits))
    if scores.max() < model.cutoff:
        return ()
    return tuple(answer(scores, model.titles, model.cutoff, model.max_titles))


@functools.cache
def _related() -> dict[str, list[str]]:
    with _LIST_PATH.open(newline="") as f:
        return {
            row["standard_title"]: [t.strip() for t in row["related_titles"].split(",") if t.strip()]
            for row in csv.DictReader(f)
        }


def related(titles: list[str]) -> list[str]:
    """The titles plus each one's related titles, without repeats, in list order."""
    out = dict.fromkeys(titles)
    for title in titles:
        out.update(dict.fromkeys(_related().get(title, [])))
    return list(out)


def list_titles() -> list[str]:
    """Every standard title on the list, in list order."""
    return list(_related())
