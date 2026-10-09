"""HTTP client for the sector classifier service (yabot-jobs-sector).

The model itself lives in its own private Cloud Run service — trained,
packaged and deployed from yabot.jobs-ml/sector-classifier (spec in that
repo's docs/deployment_plan.md, "API spec") — so the backend never loads it.
Same calling pattern as app.services.browser_fetch: a Google-signed ID token
for the service URL, and never raises: SECTOR_API_URL unset, the service
unreachable or slow, and a bad response all collapse to the same None, which
the caller treats as "pending, classify later".
"""

import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.services.browser_fetch import _identity_token_headers

logger = logging.getLogger("app.sector_api")

# Long enough to ride out a cold start (~30s measured on the first request
# after scale-from-zero) — anything slower is left pending for the sweep.
_TIMEOUT_SECONDS = 30.0


@dataclass
class SectorPrediction:
    sector: str  # one of the model's labels — the JobSector values
    confidence: float  # 0-1, for the model's top sector
    model: str  # model version, e.g. "ettin32m-2026-10-08"


def classify_remote(title: str | None, description: str | None) -> SectorPrediction | None:
    """The sector service's answer for this posting, or None on any failure."""
    if not settings.sector_api_url:
        return None
    try:
        response = httpx.post(
            f"{settings.sector_api_url}/classify",
            json={"title": title, "description": description},
            headers=_identity_token_headers(settings.sector_api_url),
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        return SectorPrediction(sector=data["sector"], confidence=float(data["confidence"]), model=data["model"])
    except Exception:  # never raises: network, auth-token, HTTP and response-shape errors alike
        logger.warning("Sector API call failed; posting left pending for the sweep.", exc_info=True)
        return None
