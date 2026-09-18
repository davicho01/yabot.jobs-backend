"""Shared HTML/text helpers used by job_scanner.py's generic JSON-LD/OG
extraction path and by every per-ATS adapter's own extract() — kept here
(not in job_scanner.py) so adapter modules can use them without importing
job_scanner.py itself (which imports from app.services.adapters.base, and
would create a circular import the other way around).
"""

import re
from html import unescape
from typing import Any

# matches JobPosting.location's column width.
MAX_LOCATION_LENGTH = 255

# Open Graph title tag — usually cleaner than a page's raw <title>, and the
# only on-page signal some ATS's (e.g. a Gem board's own page) expose for a
# thing that isn't otherwise structured anywhere (Gem's job page never
# names the company at all; only its *board* page's og:title does, as
# "{Company} Careers").
OG_TITLE_RE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', re.IGNORECASE | re.DOTALL
)

_TAG_RE = re.compile(r"<[^>]+>")

# Used by html_to_formatted_text to turn block-level HTML structure into
# readable whitespace instead of collapsing it away (see clean_text, which
# does the latter and is fine for single-line fields like title/company but
# turns a whole job description into an unreadable wall of text).
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_LI_TRAILING_BR_RE = re.compile(r"<br\s*/?>\s*</li>", re.IGNORECASE)
_LI_OPEN_RE = re.compile(r"<li[^>]*>\s*", re.IGNORECASE)
_LI_CLOSE_RE = re.compile(r"</li\s*>", re.IGNORECASE)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HEADER_OPEN_RE = re.compile(r"<h([1-6])[^>]*>", re.IGNORECASE)
_BLOCK_CLOSE_RE = re.compile(r"</(?:p|div|ul|ol|h[1-6])\s*>", re.IGNORECASE)
# Converted to Markdown equivalents (**bold**, *italic*, [text](url), #
# headings) rather than dropped outright, so the frontend's Markdown
# renderer can still show the emphasis/structure a plain-text description
# would otherwise lose entirely. Applied before the final catch-all tag
# strip below, which removes whatever's left (spans, divs, ...) with no
# Markdown equivalent to convert to.
# Public (not module-private) because Stripe's own careers-page scrape
# (app.services.adapters.stripe) also needs to rewrite <a> tags standalone,
# not just as part of html_to_formatted_text's full conversion.
LINK_RE = re.compile(r'<a\b[^>]*\bhref=["\']([^"\']*)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_LINK_RE = LINK_RE
_BOLD_OPEN_RE = re.compile(r"<(?:strong|b)\b[^>]*>", re.IGNORECASE)
_BOLD_CLOSE_RE = re.compile(r"</(?:strong|b)\s*>", re.IGNORECASE)
_EM_OPEN_RE = re.compile(r"<(?:em|i)\b[^>]*>", re.IGNORECASE)
_EM_CLOSE_RE = re.compile(r"</(?:em|i)\s*>", re.IGNORECASE)

_DIV_TAG_RE = re.compile(r"<(/?)div\b[^>]*>", re.IGNORECASE)


def extract_balanced_div(html: str, div_start: int) -> str | None:
    """Return the inner HTML of the <div ...> opening at index `div_start`,
    found by tracking nested div depth to its true matching close tag — a
    simple non-greedy regex would stop at the first nested </div> instead,
    truncating the content after only its first child element. Shared by
    the Greenhouse and Stripe adapters, both of which need to pull one
    div's full subtree out of a larger page rather than the whole page.
    """
    open_end = html.find(">", div_start)
    if open_end == -1:
        return None
    depth = 1
    for m in _DIV_TAG_RE.finditer(html, open_end + 1):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return html[open_end + 1 : m.start()]
    return None


def clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    # Unescape BEFORE stripping tags: some sites (e.g. Mastercard) double-
    # encode their JSON-LD description as HTML-entity-escaped markup
    # ("&lt;p&gt;...&lt;/p&gt;" inside the JSON string, not raw "<p>"). Tag
    # stripping first would miss those entirely, since _TAG_RE only matches
    # literal "<...>" — they'd only turn into real tags *after* stripping,
    # leaking straight into the cleaned text.
    text = _TAG_RE.sub(" ", unescape(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _tighten_bold_markers(text: str) -> str:
    """Move each "**" past any whitespace on its *inner* side (trailing for
    an opener, leading for a closer — the two alternate with each
    occurrence).

    Source HTML that wraps a heading like <strong>Title<br></strong> puts
    the closing tag right after the line break, so the naive tag->marker
    conversion above leaves the closing "**" sitting right after a
    newline: "**Title\\n**Body...". CommonMark won't treat a "**"
    preceded by whitespace as a valid closer, so that would render as a
    literal, unrendered "**" instead of ending the bold run. Must run
    after every other tag has already been substituted/stripped — while
    e.g. a <span> still sits between the newline and the marker, there's
    no whitespace for this to find yet.
    """
    segments = text.split("**")
    if len(segments) < 3:
        return text
    for i in range(1, len(segments)):
        if i % 2 == 1:  # opening marker sits between segments[i-1] and segments[i]
            stripped = segments[i].lstrip()
            segments[i - 1] += segments[i][: len(segments[i]) - len(stripped)]
            segments[i] = stripped
        else:  # closing marker sits between segments[i-1] and segments[i]
            stripped = segments[i - 1].rstrip()
            segments[i] = segments[i - 1][len(stripped) :] + segments[i]
            segments[i - 1] = stripped
    return "**".join(segments)


def html_to_formatted_text(value: Any) -> str | None:
    """Like clean_text, but for the `description` field: converts to
    Markdown rather than flattening to bare text, so paragraph breaks,
    headings, bullet lists, links, and bold/italic emphasis all survive
    (the frontend renders this field as Markdown) instead of every bit of
    structure and emphasis just vanishing along with the tags carrying it.
    """
    if not isinstance(value, str):
        return None
    # Unescape BEFORE any tag-based substitution below — see clean_text's
    # comment: some sites (Mastercard, Freedom Mortgage's Phenom-hosted
    # JSON-LD) double-encode their description as HTML-entity-escaped
    # markup ("&lt;p&gt;..." inside the JSON string, not raw "<p>"), which
    # every regex below can't see as a tag at all until unescaped first —
    # left until the end, they'd only turn into real tags *after* stripping,
    # leaking raw HTML straight into the "Markdown" the frontend renders.
    text = unescape(value)
    text = _SCRIPT_STYLE_RE.sub(" ", text)
    text = _LINK_RE.sub(r"[\2](\1)", text)
    text = _BOLD_OPEN_RE.sub("**", text)
    text = _BOLD_CLOSE_RE.sub("**", text)
    text = _EM_OPEN_RE.sub("*", text)
    text = _EM_CLOSE_RE.sub("*", text)
    text = _LI_TRAILING_BR_RE.sub("</li>", text)
    text = _LI_OPEN_RE.sub("\n- ", text)
    text = _LI_CLOSE_RE.sub("", text)
    text = _BR_RE.sub("\n", text)
    text = _HEADER_OPEN_RE.sub(lambda m: "\n\n" + "#" * int(m.group(1)) + " ", text)
    text = _BLOCK_CLOSE_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = _tighten_bold_markers(text)

    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    cleaned: list[str] = []
    for line in lines:
        if line:
            cleaned.append(line)
        elif cleaned and cleaned[-1] != "":
            cleaned.append("")
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    return "\n".join(cleaned) or None
