"""Headless-browser fallback for the handful of sites whose real content
only exists after JS runs — a plain httpx GET (see job_scanner.py,
ats_adapters.py) gets an empty shell for these (verified against ADP's
career-center pages and Ashby's white-label embed on companies that don't
happen to use their own name as the board slug).

The actual Chromium rendering lives in the standalone browser_fetch_service/
(its own deployable, with Playwright's browser binary baked into its own
image) — this module is just an HTTP client to it, so callers here never
pull in Playwright's heavy runtime footprint (seconds to launch, a few
hundred MB of browser binary, more failure-prone than every other fetch in
this codebase) themselves. Never raises: BROWSER_FETCH_SERVICE_URL unset,
the service being unreachable, and an actual render failure all collapse to
the same None return.
"""

import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings

logger = logging.getLogger("app.browser_fetch")

_TIMEOUT_SECONDS = 20.0


@dataclass
class RenderedPage:
    html: str
    # The post-redirect URL Chromium actually landed on — callers that need
    # to resolve relative links or detect ATS error-page redirects (see
    # job_scanner._fetch_html) can't rely on the URL they requested.
    url: str


def fetch_rendered_page(url: str, *, wait_for_selector: str | None = None) -> RenderedPage | None:
    """Render `url` in headless Chromium (via browser_fetch_service) and
    return the fully hydrated HTML plus the final post-redirect URL, or None
    on any failure whatsoever (service not configured, unreachable, browser
    launch failure, navigation timeout, target crash, ...). Never raises —
    every caller treats this exactly like the other best-effort fallbacks in
    this codebase (e.g. detect_embedded_ats_source): a None just means "this
    enhancement isn't available right now," not an error to surface.
    """
    if not settings.browser_fetch_service_url:
        logger.info("Browser fetch service not configured; skipping rendered fetch of %s", url)
        return None

    try:
        response = httpx.post(
            f"{settings.browser_fetch_service_url}/fetch",
            json={"url": url, "wait_for_selector": wait_for_selector},
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        if data["html"] is None:
            return None
        return RenderedPage(html=data["html"], url=data.get("final_url") or url)
    except Exception:
        logger.warning("Rendered fetch of %s failed; falling back to no enhancement.", url, exc_info=True)
        return None


def fetch_rendered_html(url: str, *, wait_for_selector: str | None = None) -> str | None:
    """Same as fetch_rendered_page, but for callers that only need the HTML."""
    rendered = fetch_rendered_page(url, wait_for_selector=wait_for_selector)
    return rendered.html if rendered else None
