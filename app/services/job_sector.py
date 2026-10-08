"""Job function/department (sector) inferred from a posting's own text.

Unlike workplace_type (read off a posting's location entries, see
app.services.workplace) this can't be derived from anything company-level:
the same company can post HR, Finance, and Engineering roles at once, so
each posting has to be classified from what it itself says it's hiring for.

Classified by a model trained in the yabot.jobs-ml project on ~8,500 real
postings labeled by job function (full title + description), replacing the
keyword lists this module used to hold. The model file, app/ml/sector_model.npz,
is loaded once per process with only numpy (app.services.sector_model) —
about 40 MB of memory and ~1.5 ms per posting, so it runs inline on every scan.

Below the model's confidence threshold (stored in the file, currently 0.5) the
answer is UNKNOWN: a wrong sector hurts a sector page more than a missing one.
If the model file can't be loaded, every posting gets UNKNOWN and the error
is logged once, rather than failing scans.

To update the model: retrain in yabot.jobs-ml, run `python -m sector_ml.export`,
copy models/sector_model.npz to app/ml/, then re-run one_off/backfill_sector.py.
"""

import logging
from functools import cache
from pathlib import Path

from app.models.enums import JobSector
from app.services.sector_model import SectorModel

logger = logging.getLogger("app.job_sector")

MODEL_PATH = Path(__file__).resolve().parent.parent / "ml" / "sector_model.npz"


@cache
def _model() -> SectorModel | None:
    try:
        return SectorModel(MODEL_PATH)
    except Exception:
        logger.exception("Sector model failed to load from %s; every posting will get sector=unknown", MODEL_PATH)
        return None


def classify_sector(title: str | None, description: str | None = None) -> JobSector:
    """The job function this posting is hiring for, from its title and full
    description. UNKNOWN when the model isn't confident enough (or can't load)."""
    model = _model()
    if model is None:
        return JobSector.UNKNOWN
    sector, _confidence = model.classify(title, description)
    return JobSector(sector)
