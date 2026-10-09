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

import html as html_lib
import logging
import re
import sys
import time
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from google.auth.exceptions import DefaultCredentialsError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.id_token import fetch_id_token

from app.core.config import settings

logger = logging.getLogger("app.browser_fetch")

# Must be at least as long as yabot-jobs-browser's own worst case (two
# sequential 35s Playwright waits plus launch overhead, verified live
# against careers.ibm.com's slow-to-clear AWS WAF challenge - see that
# service's main.py) and its own 90s Cloud Run request timeout (deploy.yml)
# - otherwise this client gives up and returns None before the render had
# any chance to actually finish.
_TIMEOUT_SECONDS = 90.0

# Some WAF/bot-management fronts (verified live: jobs.dominos.com's
# Cloudflare managed challenge) don't block every request outright - they
# score each render attempt independently, so a single failed render often
# just means this particular headless launch drew a harder challenge, not
# that the site is unreachable. Measured live against Domino's: ~59% of
# individual render attempts failed outright, but a fresh attempt right
# after routinely succeeds - a second try roughly squares that failure
# rate (~59% -> ~35%), and the caller's own outer scan-attempt backoff (see
# jobs.py's scan_retry_max_attempts) only kicks in after this whole
# function gives up, so retrying here catches the case that backoff alone
# leaves as a broken-looking page for hours.
_RENDER_ATTEMPTS = 2

# Interstitial pages bot-management fronts serve instead of the real page —
# HTTP 200 once rendered, so nothing downstream could tell them apart from a
# job page: 65 live listings were titled "Just a moment..." (Cloudflare:
# Progressive, Epic Games, Domino's, Carvana, BairesDev) or "Access Denied"
# (Akamai: Rubrik) as of 2026-10-07. Matched on the whole <title>, not a
# substring, so a real posting that merely mentions one of these can't trip it.
_BOT_CHALLENGE_TITLES = frozenset({
    "just a moment...",
    "just a moment",
    "attention required! | cloudflare",
    "please wait... | cloudflare",
    "access denied",
    "pardon our interruption",
})
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def is_bot_challenge_title(title: str | None) -> bool:
    return " ".join(html_lib.unescape(title or "").split()).lower() in _BOT_CHALLENGE_TITLES


def is_bot_challenge_page(html: str | None) -> bool:
    match = _TITLE_RE.search(html or "")
    return match is not None and is_bot_challenge_title(match.group(1))


@dataclass
class RenderedPage:
    html: str
    # The post-redirect URL Chromium actually landed on — callers that need
    # to resolve relative links or detect ATS error-page redirects (see
    # app.services.adapters.base.fetch_html) can't rely on the URL they
    # requested.
    url: str


def _identity_token_headers(audience: str) -> dict[str, str]:
    # yabot-jobs-browser is a private Cloud Run service (no --allow-
    # unauthenticated, no IAM invoker binding for anyone) — every caller
    # needs a Google-signed ID token for this exact audience. On Cloud
    # Run/Functions this comes from the ambient metadata server for free;
    # locally (no ADC configured) it just fails and we call unauthenticated,
    # which only works if BROWSER_FETCH_SERVICE_URL happens to point at an
    # unauthenticated service.
    try:
        token = fetch_id_token(GoogleAuthRequest(), audience)
    except DefaultCredentialsError:
        return {}
    return {"Authorization": f"Bearer {token}"}


# Whether a render was requested since the last reset_render_attempted() —
# job_scanner.scan_job_url resets it per scan and records the answer on the
# URL row (scanned_via_browser), so no adapter has to report it itself.
_render_attempted: ContextVar[bool] = ContextVar("render_attempted", default=False)


def reset_render_attempted() -> None:
    _render_attempted.set(False)


def render_attempted() -> bool:
    return _render_attempted.get()


def _caller_module() -> str:
    # The first module outside this one: app.services.adapters.base means
    # fetch_html's reactive fallback, any other adapter means a render that
    # adapter always makes.
    frame = sys._getframe(1)
    while frame is not None and frame.f_globals.get("__name__") == __name__:
        frame = frame.f_back
    return frame.f_globals.get("__name__", "?") if frame is not None else "?"


def _log_render(url: str, caller: str, outcome: str, attempts: int, started: float) -> None:
    # One line per fetch_rendered_page call, so per-host success rate and
    # render time (yabot-jobs-browser's cost) can be read straight off the
    # worker logs, e.g. textPayload:"browser_render" grouped by host/outcome.
    # Before this, a successful render left no trace of which URL it was for.
    logger.info(
        "browser_render host=%s outcome=%s attempts=%d seconds=%.1f caller=%s url=%s",
        urlparse(url).hostname,
        outcome,
        attempts,
        time.monotonic() - started,
        caller,
        url,
    )


def fetch_rendered_page(
    url: str, *, wait_for_selector: str | None = None, pierce_shadow: bool = False
) -> RenderedPage | None:
    """Render `url` in headless Chromium (via browser_fetch_service) and
    return the fully hydrated HTML plus the final post-redirect URL, or None
    on any failure whatsoever (service not configured, unreachable, browser
    launch failure, navigation timeout, target crash, ...). Never raises —
    every caller treats this exactly like the other best-effort fallbacks in
    this codebase (e.g. detect_embedded_ats_source): a None just means "this
    enhancement isn't available right now," not an error to surface.

    pierce_shadow: pass True for tenants whose real content only exists
    inside an open shadow root (verified live: UltiPro/UKG Pro's "Ignite"
    design system) — plain page.content() serializes the light DOM only, so
    those come back as an empty custom-element shell no matter how long you
    wait. The service walks the live DOM tree instead when this is set,
    inlining shadow content so regex-based adapters see it like any other
    markup. Leave False (the default) for everything else — it's slower and
    unnecessary when there's no shadow DOM to pierce.
    """
    if not settings.browser_fetch_service_url:
        logger.info("Browser fetch service not configured; skipping rendered fetch of %s", url)
        return None

    _render_attempted.set(True)
    caller = _caller_module()
    started = time.monotonic()
    outcome = "error"
    for attempt in range(1, _RENDER_ATTEMPTS + 1):
        try:
            response = httpx.post(
                f"{settings.browser_fetch_service_url}/fetch",
                json={"url": url, "wait_for_selector": wait_for_selector, "pierce_shadow": pierce_shadow},
                headers=_identity_token_headers(settings.browser_fetch_service_url),
                timeout=_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            if data["html"] is not None and not is_bot_challenge_page(data["html"]):
                _log_render(url, caller, "ok", attempt, started)
                return RenderedPage(html=data["html"], url=data.get("final_url") or url)
            outcome = "empty" if data["html"] is None else "challenge"
            logger.info(
                "Rendered fetch of %s came back %s (attempt %d/%d).",
                url,
                "empty" if data["html"] is None else "as a bot-challenge page",
                attempt,
                _RENDER_ATTEMPTS,
            )
        except Exception:
            outcome = "error"
            logger.warning(
                "Rendered fetch of %s failed (attempt %d/%d).", url, attempt, _RENDER_ATTEMPTS, exc_info=True
            )

    logger.warning("Rendered fetch of %s exhausted every attempt; falling back to no enhancement.", url)
    _log_render(url, caller, outcome, _RENDER_ATTEMPTS, started)
    return None


def fetch_rendered_html(
    url: str, *, wait_for_selector: str | None = None, pierce_shadow: bool = False
) -> str | None:
    """Same as fetch_rendered_page, but for callers that only need the HTML."""
    rendered = fetch_rendered_page(url, wait_for_selector=wait_for_selector, pierce_shadow=pierce_shadow)
    return rendered.html if rendered else None
