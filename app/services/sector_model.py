"""Sector model inference with only numpy and the standard library.

Copied from the yabot.jobs-ml project (sector_ml/predict_numpy.py) — edit it
there, not here, so training and inference stay identical. The model file it
loads (app/ml/sector_model.npz) is produced there by `python -m sector_ml.export`.

Reproduces exactly what the trained scikit-learn pipeline does: clean the
text, split it into word / character n-grams, weight them (TF-IDF, l2
normalized), score each sector with the classifier's weights, softmax, and
answer "unknown" below the confidence threshold.

    from app.services.sector_model import SectorModel
    model = SectorModel("app/ml/sector_model.npz")
    model.classify("Registered Nurse - ICU", "<p>Provide patient care...</p>")
    # -> ("healthcare", 0.97)
"""

import html
import json
import math
import re

import numpy as np

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"(?u)\b\w\w+\b")  # scikit-learn's default token_pattern
_WHITE_SPACES = re.compile(r"\s\s+")  # scikit-learn's char_wb whitespace collapse

UNKNOWN = "unknown"


def clean(text, max_chars=None):
    """Same cleanup as training (sector_ml/text.py)."""
    if not isinstance(text, str) or not text:
        return ""
    text = _TAG_RE.sub(" ", html.unescape(text))
    text = _WS_RE.sub(" ", text).strip()
    return text[:max_chars] if max_chars else text


def _word_ngrams(text, ngram_range, stop_words):
    tokens = _TOKEN_RE.findall(text.lower())
    if stop_words:
        tokens = [t for t in tokens if t not in stop_words]
    min_n, max_n = ngram_range
    if max_n == 1:
        return tokens
    out = tokens[:] if min_n == 1 else []
    for n in range(max(min_n, 2), max_n + 1):
        out += [" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]
    return out


def _char_wb_ngrams(text, ngram_range):
    text = _WHITE_SPACES.sub(" ", text.lower())
    min_n, max_n = ngram_range
    out = []
    for w in text.split():
        w = " " + w + " "
        w_len = len(w)
        for n in range(min_n, max_n + 1):
            offset = 0
            out.append(w[offset : offset + n])
            while offset + n < w_len:
                offset += 1
                out.append(w[offset : offset + n])
            if offset == 0:  # word shorter than n: counted once
                break
    return out


class SectorModel:
    def __init__(self, path):
        data = np.load(path)
        meta = json.loads(data["meta"].tobytes().decode())
        self.classes = meta["classes"]
        self.threshold = meta["threshold"]
        self.coef = data["coef"].astype(np.float64)
        self.intercept = data["intercept"].astype(np.float64)
        stop = frozenset(meta["english_stop_words"])
        self.features = []
        offset = 0
        for f in meta["features"]:
            vocab = {term: i for i, term in enumerate(f["vocabulary"])}
            self.features.append(
                {
                    "column": f["column"],
                    "analyzer": f["analyzer"],
                    "ngram_range": tuple(f["ngram_range"]),
                    "stop_words": stop if f["stop_words"] else None,
                    "sublinear_tf": f["sublinear_tf"],
                    "weight": f["weight"],
                    "vocab": vocab,
                    "idf": data[f"idf_{f['name']}"].astype(np.float64),
                    "offset": offset,
                }
            )
            offset += len(vocab)
        self.n_features = offset

    def _vectorize(self, title, description):
        texts = (clean(title, 300), clean(description))
        x = np.zeros(self.n_features)
        for f in self.features:
            text = texts[f["column"]]
            grams = _word_ngrams(text, f["ngram_range"], f["stop_words"]) if f["analyzer"] == "word" else _char_wb_ngrams(text, f["ngram_range"])
            counts = {}
            vocab = f["vocab"]
            for g in grams:
                j = vocab.get(g)
                if j is not None:
                    counts[j] = counts.get(j, 0) + 1
            if not counts:
                continue
            idx = np.fromiter(counts.keys(), dtype=np.int64)
            tf = np.fromiter(counts.values(), dtype=np.float64)
            if f["sublinear_tf"]:
                tf = 1.0 + np.log(tf)
            v = tf * f["idf"][idx]
            v /= math.sqrt(float(v @ v))
            x[f["offset"] + idx] = v * f["weight"]
        return x

    def probabilities(self, title, description=None):
        scores = self.coef @ self._vectorize(title, description) + self.intercept
        scores -= scores.max()
        p = np.exp(scores)
        return p / p.sum()

    def classify(self, title, description=None):
        """(sector, confidence); "unknown" when confidence < threshold."""
        p = self.probabilities(title, description)
        best = int(p.argmax())
        conf = float(p[best])
        sector = self.classes[best] if conf >= self.threshold else UNKNOWN
        return sector, conf
