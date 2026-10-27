"""Deterministic SEO checks on the collection pages, run by the static
generator before it publishes them (spec §22, §33, §56). No model is asked:
these are the rules an SEO agent may advise on later but never override.

Per rendered page (check_page): one title, description, H1 and canonical
URL; the canonical is the page's own URL; the robots directive says what
the collection's indexability says; the jobs are in the HTML; the JSON-LD
parses and has no JobPosting (ADR-011: job markup belongs on job pages).
Across pages (check_collections): no two indexed pages share a title or
description, and no two indexed collections list nearly the same jobs
(duplicate intent, §22).

An ERROR means the page is broken for search: the generator keeps that
collection out of the index for the run (noindex, no sitemap entry, no
links to it) without changing its status, so it's back once fixed. A
WARNING is reported only. Both go to REPORT_KEY for review.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from enum import StrEnum
from html.parser import HTMLParser
from itertools import combinations
from typing import TYPE_CHECKING

from app.services.collection_qualification import THRESHOLDS

if TYPE_CHECKING:
    from app.services.static_collection_pages import CollectionPage

REPORT_KEY = "_meta/seo-report.json"
TITLE_MAX = 70  # characters, brand included; longer gets cut off in results
DESCRIPTION_MIN = 70
DESCRIPTION_MAX = 170


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    path: str
    code: str
    severity: Severity
    detail: str = ""

    def as_dict(self) -> dict:
        return asdict(self) | {"severity": self.severity.value}


def _error(path: str, code: str, detail: str = "") -> Issue:
    return Issue(path, code, Severity.ERROR, detail)


def _warning(path: str, code: str, detail: str = "") -> Issue:
    return Issue(path, code, Severity.WARNING, detail)


class _Page(HTMLParser):
    """What the checks read from a page's HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.titles: list[str] = []
        self.h1s = 0
        self.description: str | None = None
        self.canonicals: list[str] = []
        self.robots: str | None = None
        self.job_links = 0
        self.ld_json: list[str] = []
        self._in: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "title":
            self._in, self._text = "title", []
        elif tag == "h1":
            self.h1s += 1
        elif tag == "meta" and a.get("name") == "description":
            self.description = a.get("content", "")
        elif tag == "meta" and a.get("name") == "robots":
            self.robots = a.get("content", "")
        elif tag == "link" and a.get("rel") == "canonical":
            self.canonicals.append(a.get("href", ""))
        elif tag == "a" and "job-rows__title" in a.get("class", "").split():
            self.job_links += 1
        elif tag == "script" and a.get("type") == "application/ld+json":
            self._in, self._text = "ld", []

    def handle_data(self, data: str) -> None:
        if self._in:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._in == "title" and tag == "title":
            self.titles.append("".join(self._text).strip())
            self._in = None
        elif self._in == "ld" and tag == "script":
            self.ld_json.append("".join(self._text))
            self._in = None


def check_page(path: str, html: str, *, url: str, indexable: bool) -> list[Issue]:
    """The issues on one rendered page at `path` (served at `url`)."""
    page = _Page()
    page.feed(html)
    issues = []
    if len(page.titles) != 1 or not page.titles[0]:
        issues.append(_error(path, "title", f"{len(page.titles)} title(s)"))
    elif len(page.titles[0]) > TITLE_MAX:
        issues.append(_warning(path, "title_length", f"{len(page.titles[0])} characters"))
    if not page.description:
        issues.append(_error(path, "description", "missing"))
    elif not DESCRIPTION_MIN <= len(page.description) <= DESCRIPTION_MAX:
        issues.append(_warning(path, "description_length", f"{len(page.description)} characters"))
    if page.h1s != 1:
        issues.append(_error(path, "h1", f"{page.h1s} H1s"))
    if page.canonicals != [url]:
        issues.append(_error(path, "canonical", ", ".join(page.canonicals) or "missing"))
    expected = "index, follow" if indexable else "noindex, follow"
    if page.robots != expected:
        issues.append(_error(path, "robots", f"{page.robots!r}, expected {expected!r}"))
    if not page.job_links:
        issues.append(_error(path, "no_jobs", "no job rows in the HTML"))
    for raw in page.ld_json:
        try:
            data = json.loads(raw)
        except ValueError as e:
            issues.append(_error(path, "structured_data", f"JSON-LD doesn't parse: {e}"))
            continue
        if "JobPosting" in json.dumps(data):
            issues.append(_error(path, "job_posting_markup", "JobPosting on a listing page"))
    return issues


def check_collections(pages: list[CollectionPage]) -> list[Issue]:
    """Issues between the indexed collections: shared titles or
    descriptions on their first pages, nearly the same jobs (sharing at
    least THRESHOLDS.near_duplicate_share of the longer list, so a remote
    page that's a small part of its role page is fine), and too few jobs to
    be worth indexing."""
    indexed = [p for p in pages if p.indexable]
    issues = []
    for attr in ("title", "description"):
        seen: dict[str, str] = {}
        for page in indexed:
            value = getattr(page, attr)
            if value in seen:
                issues.append(_warning(page.path, f"duplicate_{attr}", f"same as {seen[value]}"))
            else:
                seen[value] = page.path
    # Only pairs that share a job, counted through each job's collections.
    sizes = {p.path: len(p.jobs) for p in indexed}
    by_job: dict[str, list[str]] = defaultdict(list)
    for page in indexed:
        for job in page.jobs:
            by_job[job.url_id].append(page.path)
    shared = Counter(pair for paths in by_job.values() for pair in combinations(sorted(paths), 2))
    for (a, b), n in sorted(shared.items()):
        if n / max(sizes[a], sizes[b]) >= THRESHOLDS.near_duplicate_share:
            issues.append(_warning(b, "duplicate_intent", f"nearly the same jobs as {a}"))
    for page in indexed:
        if page.stats.open_jobs < THRESHOLDS.min_jobs:
            issues.append(_warning(page.path, "thin", f"{page.stats.open_jobs} open jobs"))
    return issues


def errors_by_collection(pages: list[CollectionPage], issues: list[Issue]) -> set[str]:
    """Page-1 paths of the collections with an ERROR on any of their pages."""
    broken = {i.path for i in issues if i.severity is Severity.ERROR}
    return {p.path for p in pages if broken & set(p.paths())}


def report(issues: list[Issue], pages_checked: int, checked_at: str) -> dict:
    """REPORT_KEY's content."""
    return {
        "checked_at": checked_at,
        "pages": pages_checked,
        "errors": sum(i.severity is Severity.ERROR for i in issues),
        "warnings": sum(i.severity is Severity.WARNING for i in issues),
        "issues": [i.as_dict() for i in sorted(issues, key=lambda i: (i.severity != Severity.ERROR, i.path, i.code))],
    }
