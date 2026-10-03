"""Logo images: turn any image into the 128px square PNG we store and
serve (normalize), and pull one from what an admin gives us — a pasted URL
(an image, or a page whose logo we find the way browsers do) or an uploaded
file. Automatic logos come from logo.dev instead (app.services.logo_dev);
both end up stored the same way by app.services.company_logos.

SVG is not accepted: rasterizing it safely needs a native library, and
serving raw SVG from our domain could carry scripts.
"""

from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; YabotJobsBot/1.0)"
TIMEOUT = 10.0
MAX_HTML_BYTES = 2_000_000
MAX_IMAGE_BYTES = 1_000_000
# Smaller than this on its short side and the avatar looks better than an
# upscaled 16/32px favicon (automatic logos; see MANUAL_MIN_SIDE).
MIN_SIDE = 48
# Wider (or taller) than this is a wordmark banner, not something that reads
# at 16-56px next to a name.
MAX_ASPECT = 4.0
# A schema.org microdata itemprop="image" is sometimes the logo (google.com's
# is its "G") and sometimes a hero photo, so it's only taken if it's about
# square — the shape of a logo mark, unlike a photo.
SQUARE_ASPECT = 1.25
OUTPUT_SIZE = 128

STATUS_OK = "ok"
STATUS_NONE = "none"  # no logo exists for it
STATUS_ERROR = "error"  # lookup failed; retried on a later run


@dataclass
class LogoResult:
    status: str
    png: bytes | None = None
    source_url: str | None = None


_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([a-zA-Z:-]+)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s>]+))')
_JSON_LD_RE = re.compile(
    r'<script[^>]+type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL
)
_BASE_HREF_RE = re.compile(r'<base\b[^>]*href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


def _attrs(tag: str) -> dict[str, str]:
    return {
        m.group(1).lower(): unescape(m.group(3) if m.group(3) is not None else m.group(4) if m.group(4) is not None else m.group(5))
        for m in _ATTR_RE.finditer(tag)
    }


def _largest_size(sizes: str | None) -> int:
    """'180x180' / '16x16 32x32' / 'any' -> the largest side declared (0 if
    unknown). 'any' means scalable (SVG), which we can't use anyway."""
    best = 0
    for token in (sizes or "").lower().split():
        match = re.fullmatch(r"(\d+)x(\d+)", token)
        if match:
            best = max(best, min(int(match.group(1)), int(match.group(2))))
    return best


def _is_svg(url: str, mime: str | None = None) -> bool:
    return (mime or "").lower().startswith("image/svg") or urlsplit(url).path.lower().endswith(".svg")


def _org_logos(html: str) -> list[str]:
    """Logo URLs from schema.org Organization-ish JSON-LD blocks."""
    found: list[str] = []

    def visit(node) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if any(isinstance(t, str) and t in {"Organization", "Corporation", "LocalBusiness", "Brand"} for t in types):
                logo = node.get("logo")
                if isinstance(logo, dict):
                    logo = logo.get("url") or logo.get("contentUrl")
                if isinstance(logo, str) and logo.strip():
                    found.append(logo.strip())
            for value in node.values():
                if isinstance(value, (dict, list)):
                    visit(value)

    for block in _JSON_LD_RE.findall(html):
        try:
            visit(json.loads(block.strip()))
        except (ValueError, RecursionError):
            continue
    return found


def _microdata(html: str, base: str) -> tuple[list[str], list[str]]:
    """(itemprop="logo" URLs, itemprop="image" URLs) from <meta>/<link> tags."""
    logos: list[str] = []
    images: list[str] = []
    for tag in _META_TAG_RE.findall(html) + _LINK_TAG_RE.findall(html):
        attrs = _attrs(tag)
        prop = attrs.get("itemprop", "").lower()
        value = attrs.get("content") or attrs.get("href")
        if not value or prop not in ("logo", "image"):
            continue
        (logos if prop == "logo" else images).append(urljoin(base, value))
    return logos, images


def square_only_candidates(html: str, page_url: str) -> set[str]:
    """The candidates that only count if square (see SQUARE_ASPECT)."""
    return set(_microdata(html, page_url)[1])


def candidates(html: str, page_url: str) -> list[str]:
    """Absolute candidate icon URLs from a homepage, best first."""
    base = page_url
    base_match = _BASE_HREF_RE.search(html)
    if base_match:
        base = urljoin(page_url, unescape(base_match.group(1)))

    touch: list[tuple[int, str]] = []
    icons: list[tuple[int, str]] = []
    for tag in _LINK_TAG_RE.findall(html):
        attrs = _attrs(tag)
        rel = attrs.get("rel", "").lower().split()
        href = attrs.get("href")
        if not href or _is_svg(href, attrs.get("type")):
            continue
        size = _largest_size(attrs.get("sizes"))
        if "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel:
            touch.append((size or 180, urljoin(base, href)))  # undeclared is conventionally 180
        elif "icon" in rel and (size >= 64 or size == 0):
            # Undeclared size (common: rel="shortcut icon" with no sizes) is
            # tried after the declared-large ones; normalize() still rejects
            # it if it turns out to be a 16/32px favicon.
            icons.append((size, urljoin(base, href)))

    micro_logos, micro_images = _microdata(html, base)
    ordered = [urljoin(base, u) for u in _org_logos(html)] + micro_logos
    ordered += [url for _, url in sorted(touch, reverse=True)]
    ordered += [url for _, url in sorted(icons, reverse=True)]
    ordered += micro_images
    root = f"{urlsplit(page_url).scheme}://{urlsplit(page_url).netloc}"
    ordered += [f"{root}/apple-touch-icon.png", f"{root}/favicon.ico"]

    seen: set[str] = set()
    result = []
    for url in ordered:
        if url.startswith(("http://", "https://")) and not _is_svg(url) and url not in seen:
            seen.add(url)
            result.append(url)
    return result


class LogoRejected(ValueError):
    """Why an image (or URL) can't be a logo, in words an admin can act on."""


def normalize_or_raise(data: bytes, *, max_aspect: float = MAX_ASPECT, min_side: int = MIN_SIDE) -> bytes:
    """Any raster image -> a 128x128 PNG with the logo centered on a
    transparent square. Raises LogoRejected saying why not: not an image,
    too small, or not a logo shape."""
    try:
        image = Image.open(io.BytesIO(data))
        if image.format == "ICO":
            # Pick the largest frame an .ico carries (often 16/32/48/256).
            sizes = sorted(image.info.get("sizes") or [image.size], key=lambda s: s[0] * s[1])
            image.size = sizes[-1]
        image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        if data.lstrip()[:5].lower() in (b"<svg ", b"<?xml") or b"<svg" in data[:1000].lower():
            raise LogoRejected("SVG isn't supported — use a PNG, JPG, WebP or ICO.") from exc
        raise LogoRejected("That isn't an image we can read (PNG, JPG, WebP, GIF or ICO).") from exc

    image = image.convert("RGBA")
    bbox = image.getchannel("A").getbbox()  # trim transparent padding
    if bbox is None:
        raise LogoRejected("The image is completely transparent.")
    image = image.crop(bbox)
    width, height = image.size
    if min(width, height) < min_side:
        raise LogoRejected(f"Too small ({width}×{height}) — use one at least {min_side}px on its shorter side.")
    if max(width, height) / min(width, height) > max_aspect:
        raise LogoRejected(f"Too wide/tall for a logo ({width}×{height}) — use the square icon version.")

    image.thumbnail((OUTPUT_SIZE, OUTPUT_SIZE), Image.LANCZOS)
    canvas = Image.new("RGBA", (OUTPUT_SIZE, OUTPUT_SIZE), (0, 0, 0, 0))
    canvas.paste(image, ((OUTPUT_SIZE - image.width) // 2, (OUTPUT_SIZE - image.height) // 2), image)
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


def normalize(data: bytes, max_aspect: float = MAX_ASPECT) -> bytes | None:
    """normalize_or_raise for the automatic crawl, where a rejected
    candidate just means "try the next one"."""
    try:
        return normalize_or_raise(data, max_aspect=max_aspect)
    except LogoRejected:
        return None


@dataclass
class _Fetched:
    url: str  # after redirects
    content: bytes
    content_type: str


def _get_capped(
    client: httpx.Client, url: str, cap: int, *, truncate: bool = False, status_out: list[int] | None = None
) -> _Fetched | None:
    """GET with a byte cap — streamed, so a huge response is never read
    whole. Past the cap an image is abandoned; a page (truncate=True) keeps
    its first `cap` bytes, which is where its <head> and icon tags are.
    None for anything but a 200."""
    with client.stream("GET", url) as response:
        if status_out is not None:
            status_out.append(response.status_code)
        if response.status_code != 200:
            return None
        body = bytearray()
        for chunk in response.iter_bytes():
            body += chunk
            if len(body) > cap:
                if not truncate:
                    return None
                del body[cap:]
                break
        return _Fetched(str(response.url), bytes(body), response.headers.get("content-type", ""))


def _new_client(user_agent: str = USER_AGENT) -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": user_agent, "Accept": "*/*"})


# ---------------------------------------------------------------------------
# A logo from a URL an admin pasted (an image, or any page that has one)
# ---------------------------------------------------------------------------

# A person picked this logo, so a small-but-real icon is fine — the automatic
# crawl's higher bar only exists to skip blurry favicons nobody chose.
MANUAL_MIN_SIDE = 32
MAX_UPLOAD_BYTES = 2_000_000


def logo_from_url(url: str, *, client: httpx.Client | None = None) -> LogoResult:
    """The logo at `url`: the image itself, or — for a web page — the best
    logo on that page (same candidates as the crawler). Raises LogoRejected
    with a reason. No robots.txt check: a person asked for this one URL."""
    url = url.strip()
    if "://" not in url:
        url = f"https://{url}"
    if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).hostname:
        raise LogoRejected("That doesn't look like a web address.")
    own_client = client is None
    client = client or _new_client()
    try:
        status: list[int] = []
        try:
            fetched = _get_capped(client, url, MAX_HTML_BYTES, truncate=True, status_out=status)
        except httpx.HTTPError as exc:
            raise LogoRejected(f"Couldn't load that address ({type(exc).__name__}).") from exc
        if fetched is None:
            code = status[0] if status else None
            if code in (401, 403, 429):
                reason = f"That site refused our request (HTTP {code})"
            elif code == 404:
                reason = "Nothing at that address (HTTP 404)"
            else:
                reason = f"Couldn't load that address (HTTP {code})"
            raise LogoRejected(f"{reason} — try the image's own address, or download it and upload the file.")

        if "html" not in fetched.content_type.lower():
            png = normalize_or_raise(fetched.content, min_side=MANUAL_MIN_SIDE)
            return LogoResult(STATUS_OK, png=png, source_url=fetched.url)

        html = fetched.content.decode("utf-8", errors="replace")
        square_only = square_only_candidates(html, fetched.url)
        last_reason = "No logo found on that page — paste the image's own address, or upload it."
        for candidate in candidates(html, fetched.url)[:6]:
            try:
                image = _get_capped(client, candidate, MAX_IMAGE_BYTES)
            except httpx.HTTPError:
                continue
            if image is None or _is_svg(image.url, image.content_type):
                continue
            try:
                png = normalize_or_raise(
                    image.content,
                    min_side=MANUAL_MIN_SIDE,
                    max_aspect=SQUARE_ASPECT if candidate in square_only else MAX_ASPECT,
                )
            except LogoRejected as exc:
                last_reason = f"The logo on that page was rejected: {exc}"
                continue
            return LogoResult(STATUS_OK, png=png, source_url=candidate)
        raise LogoRejected(last_reason)
    finally:
        if own_client:
            client.close()


def logo_from_upload(data: bytes) -> bytes:
    if len(data) > MAX_UPLOAD_BYTES:
        raise LogoRejected("That file is over 2 MB.")
    return normalize_or_raise(data, min_side=MANUAL_MIN_SIDE)
