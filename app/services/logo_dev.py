"""logo.dev's server-side API — where automatic company logos come from (see
app.services.company_logos.sync_company_logos).

Used the way logo.dev's self-hosting docs describe: look a brand up with the
secret key, download the file from the temporary URL it returns, and keep
our own copy (never the temporary URL). Each lookup returns an etag, so a
re-check whose etag hasn't changed skips the download. Page views never
reach logo.dev — only these lookups do, about one per company per re-check.

Companies we have no domain for (most Greenhouse/Lever/Ashby boards) are
looked up by name with the search endpoint first; see find_domain.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.services import logo_images
from app.services.job_dedup import normalize_company_name, strip_entity_code

logger = logging.getLogger(__name__)

API_BASE = "https://api.logo.dev"
TIMEOUT = 15.0

STATUS_OK = logo_images.STATUS_OK
STATUS_UNCHANGED = "unchanged"  # same etag as what we already store
STATUS_NONE = logo_images.STATUS_NONE
STATUS_PENDING = "pending"  # logo.dev is still indexing the brand (202)
STATUS_ERROR = logo_images.STATUS_ERROR


@dataclass
class LogoDevResult:
    status: str
    png: bytes | None = None
    etag: str | None = None


def is_configured() -> bool:
    return bool(settings.logo_dev_secret_key)


def new_client() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT,
        follow_redirects=True,
        headers={"Authorization": f"Bearer {settings.logo_dev_secret_key}", "User-Agent": logo_images.USER_AGENT},
    )


def fetch_logo(domain: str, *, known_etag: str | None = None, client: httpx.Client) -> LogoDevResult:
    try:
        response = client.get(f"{API_BASE}/v2/brands/logo", params={"domain": domain})
    except httpx.HTTPError as exc:
        logger.info("logo.dev lookup failed for %s (%s)", domain, exc)
        return LogoDevResult(STATUS_ERROR)
    if response.status_code == 404:
        return LogoDevResult(STATUS_NONE)
    if response.status_code == 202:
        return LogoDevResult(STATUS_PENDING)
    if response.status_code != 200:
        logger.info("logo.dev lookup for %s returned HTTP %s", domain, response.status_code)
        return LogoDevResult(STATUS_ERROR)
    try:
        data = response.json()["data"]
        url, etag = data["url"], data.get("etag")
    except (ValueError, KeyError, TypeError):
        return LogoDevResult(STATUS_ERROR)
    if etag and etag == known_etag:
        return LogoDevResult(STATUS_UNCHANGED, etag=etag)

    separator = "&" if "?" in url else "?"
    try:
        image = download(f"{url}{separator}format=png&size=256")
    except httpx.HTTPError as exc:
        logger.info("logo.dev download failed for %s (%s)", domain, exc)
        return LogoDevResult(STATUS_ERROR)
    if image.status_code != 200 or len(image.content) > logo_images.MAX_UPLOAD_BYTES:
        return LogoDevResult(STATUS_ERROR)
    png = logo_images.normalize(image.content)
    if png is None:
        return LogoDevResult(STATUS_NONE, etag=etag)
    return LogoDevResult(STATUS_OK, png=png, etag=etag)


def download(url: str) -> httpx.Response:
    """GET logo.dev's temporary download URL. Deliberately not the API
    client: the URL is pre-signed, and our secret key must never be sent to
    wherever it points."""
    return httpx.get(url, timeout=TIMEOUT, follow_redirects=True)


def find_domain(company_name: str, company_key: str, *, client: httpx.Client) -> str | None:
    """The domain logo.dev's brand search has for this company: the
    highest-ranked result whose name normalizes to exactly our company_key.
    Results come ranked by relevance (prominence), and lower-ranked
    namesakes are common (verified: "Plaid" also returns a "PLAID" on
    plaid.co.jp, "Ramp" a second "Ramp" on rampnetwork.com) — the top exact
    match is the well-known one. A wrong pick is fixed with a manual logo."""
    # A legal-entity code ("2100 NVIDIA USA") never matches a brand name.
    cleaned = strip_entity_code(company_name)
    if cleaned != company_name:
        company_name, company_key = cleaned, normalize_company_name(cleaned) or company_key
    try:
        response = client.get(f"{API_BASE}/search", params={"q": company_name})
        results = response.json() if response.status_code == 200 else []
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("logo.dev search failed for %s (%s)", company_name, exc)
        return None
    if isinstance(results, dict):  # tolerate a {"data": [...]} envelope
        results = results.get("data") or results.get("results") or []
    for result in results:
        if (
            isinstance(result, dict)
            and isinstance(result.get("domain"), str)
            and normalize_company_name(result.get("name")) == company_key
        ):
            return result["domain"].lower()
    return None
