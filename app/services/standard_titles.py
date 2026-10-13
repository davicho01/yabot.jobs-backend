"""Standard job titles for a posting or a search, from our own model.

Every Tech posting gets 1-3 titles from a fixed list of 103 standard tech
titles ("Senior Software Engineer", "DevOps Engineer", ...), and a typed
search is translated into the same titles ("swe" -> Software Engineer).
Search then matches postings tagged with those titles or their related ones.

The model (Ettin 32M, trained in yabot.jobs-ml/standard-title) runs here, in
process, from an ONNX file baked into the backend image: the API uses it for
searches, the workers for postings. Its folder (settings.
standard_title_model_dir) holds model.onnx, tokenizer.json and run.json (the
title list in output order, the cutoff, max lengths and the version).

Input is built exactly as in training (yabot.jobs-ml standard-title/st_ml/
predict.py posting_input and st_ml/text.py clean): a posting is
"title [SEP] cleaned description" cut at 512 tokens, a search is the typed
text cut at 64. Answer: the top title always, plus others scoring at least
the cutoff, at most 3.

Never raises: a missing model folder or any failure returns None and logs,
like app.services.sector_api. Callers treat None as "untagged, retry later"
(postings) or "literal title match only" (searches).
"""

import html
import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.core.config import settings

logger = logging.getLogger("app.standard_titles")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_DESCRIPTION_MAX_CHARS = 4000  # st_ml.predict.posting_input


@dataclass(frozen=True)
class _Model:
    session: object  # onnxruntime.InferenceSession
    # One tokenizer per input kind: truncation length is tokenizer state, so
    # sharing one between threads could cut a posting at a search's length.
    posting_tokenizer: object  # tokenizers.Tokenizer, truncating at 512
    search_tokenizer: object  # tokenizers.Tokenizer, truncating at 64
    titles: list[str]
    cutoff: float
    max_titles: int
    separator: str
    version: str


_model: _Model | None = None
_load_failed = False
_lock = threading.Lock()


def _clean(text: str | None, max_chars: int | None = None) -> str:
    """st_ml.text.clean: HTML tags out, entities decoded, whitespace collapsed."""
    if not isinstance(text, str) or not text:
        return ""
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", html.unescape(text))).strip()
    return text[:max_chars] if max_chars else text


def _normalize(text: str | None) -> str:
    return " ".join(str(text or "").split())


def _load() -> _Model | None:
    """The model, loaded once per process. None if it isn't available."""
    global _model, _load_failed
    if _model is not None or _load_failed:
        return _model
    with _lock:
        if _model is not None or _load_failed:
            return _model
        folder = Path(settings.standard_title_model_dir)
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            run = json.loads((folder / "run.json").read_text())
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1  # workers and the API each handle one request at a time
            session = ort.InferenceSession(str(folder / run["file"]), options, providers=["CPUExecutionProvider"])
            tokenizers = []
            for max_length in (run["posting_max_length"], run["search_max_length"]):
                tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
                tokenizer.enable_truncation(int(max_length))
                tokenizers.append(tokenizer)
            _model = _Model(
                session=session,
                posting_tokenizer=tokenizers[0],
                search_tokenizer=tokenizers[1],
                titles=run["titles"],
                cutoff=float(run["cutoff"]),
                max_titles=int(run["max_titles"]),
                separator=run["separator"],
                version=run["version"],
            )
            logger.info("Loaded standard-title model %s from %s", _model.version, folder)
        except Exception:
            _load_failed = True  # don't retry the load on every posting; a new process (deploy) tries again
            logger.warning("Standard-title model not available at %s; postings stay untagged.", folder, exc_info=True)
        return _model


def answer(scores: np.ndarray, titles: list[str], cutoff: float, max_titles: int) -> list[str]:
    """The top title always, then others scoring at least the cutoff, at most max_titles in all."""
    order = np.argsort(-scores)[:max_titles]
    return [titles[order[0]]] + [titles[i] for i in order[1:] if scores[i] >= cutoff]


def _tag(model: _Model, tokenizer, text: str) -> list[str] | None:
    try:
        ids = np.array([tokenizer.encode(text).ids], dtype=np.int64)
        logits = model.session.run(None, {"input_ids": ids, "attention_mask": np.ones_like(ids)})[0][0]
        return answer(1 / (1 + np.exp(-logits)), model.titles, model.cutoff, model.max_titles)
    except Exception:
        logger.warning("Standard-title model failed on an input; left untagged.", exc_info=True)
        return None


def tag_posting(title: str | None, description: str | None) -> list[str] | None:
    """1-3 standard titles for a posting, or None if the model isn't available."""
    model = _load()
    if model is None:
        return None
    text = f"{_normalize(title)} {model.separator} {_clean(description, _DESCRIPTION_MAX_CHARS)}"
    return _tag(model, model.posting_tokenizer, text)


def tag_search(text: str | None) -> list[str] | None:
    """1-3 standard titles for a typed search, or None if the model isn't available."""
    model = _load()
    if model is None or not _normalize(text):
        return None
    return _tag(model, model.search_tokenizer, _normalize(text))


def model_version() -> str | None:
    """The loaded model's version (stored with each posting's titles), or None."""
    model = _load()
    return model.version if model else None
