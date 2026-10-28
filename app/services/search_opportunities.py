"""What people search on Google when yabot.jobs shows up, grouped by what
they were after (spec §31, PR-17a): deterministic, no model. Run nightly by
collection_maintenance.py, after the page import (search_console.ingest),
into SearchOpportunity; the admin page lists them, and discovery takes the
role pages people searched for first.

A search is read word by word (geo's normalization: lowercase, no accents
or punctuation):

  role      the longest standard tech title in it ("senior software
            engineers"), plus "remote" or a place left over -> the
            collection that would answer it
  company   only a company with open jobs ("zendesk careers")
  location  only a place, or "remote" ("jobs in houston", "remote jobs")
  other     anything else ("remote rn jobs": not a tech title, so no page
            can answer it until roles go beyond tech, PR-12)

Words like "jobs", "careers", "hiring", "near me" (and Spanish "empleos",
"trabajos") are dropped before a place or company is looked for. Google
leaves out searches too rare to show, so these add up to less than the
page totals.
"""

from __future__ import annotations

import functools
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.enums import WorkplaceType
from app.models.search_opportunity import SearchOpportunity
from app.services import collection_admin, geo, job_collections, standard_titles
from app.services.collection_dimensions import REMOTE_SLUG
from app.services.search_console import page_path
from app.services.static_job_pages import LinkJob, eastern, slugify

TOP_QUERIES = 10
TOP_PAGES = 3

REMOTE_WORDS = frozenset({"remote", "wfh", "telecommute", "telework", "virtual"})
REMOTE_PHRASES = (("work", "from", "home"), ("work", "at", "home"))
FILLER_WORDS = frozenset({
    "job", "jobs", "career", "careers", "hiring", "hire", "openings", "opening", "opportunities", "opportunity",
    "position", "positions", "vacancies", "vacancy", "employment", "recruiting", "apply", "now", "immediately",
    "near", "me", "in", "at", "for", "the", "a", "an", "and", "or", "of", "us", "usa",
    "empleo", "empleos", "trabajo", "trabajos", "en", "de",
})


@dataclass(frozen=True)
class Intent:
    kind: str  # role | location | company | other
    key: str
    label: str
    collection_key: str | None = None
    role: str | None = None  # role slug
    location: str | None = None  # location slug, or "remote"
    company: str | None = None  # company_key


@functools.cache
def _role_phrases() -> dict[tuple[str, ...], str]:
    """{words: role slug} for every standard title and its plural."""
    out: dict[tuple[str, ...], str] = {}
    for title in standard_titles.list_titles():
        words = tuple(geo.search_key(title).split())
        out[words] = out[words[:-1] + (words[-1] + "s",)] = slugify(title)
    return out


def _take_role(words: list[str]) -> tuple[str | None, list[str]]:
    """The longest title in `words`, and the words around it."""
    phrases = _role_phrases()
    for n in range(min(len(words), max(map(len, phrases))), 0, -1):
        for i in range(len(words) - n + 1):
            if (slug := phrases.get(tuple(words[i:i + n]))) is not None:
                return slug, words[:i] + words[i + n:]
    return None, words


def _take_remote(words: list[str]) -> tuple[bool, list[str]]:
    for phrase in REMOTE_PHRASES:
        n = len(phrase)
        for i in range(len(words) - n + 1):
            if tuple(words[i:i + n]) == phrase:
                return True, words[:i] + words[i + n:]
    rest = [w for w in words if w not in REMOTE_WORDS]
    return len(rest) < len(words), rest


def _place(text: str) -> geo.Metro | None:
    """The area `text` names: a state, or a city by its own name — not by
    one of geo's alternate names, which a search word can collide with
    ("google" is an alternate name of a place near Topeka)."""
    found = geo.resolve_entry(text)
    if found.geo == "state":
        return found.state
    if found.geo != "city":
        return None
    if found.place is not None:  # None: a well-known area name ("bay area")
        name = geo.search_key(found.place.name)
        if not (f"{text} ".startswith(f"{name} ") or f"{name} ".startswith(f"{text} ")):
            return None
    return found.metro or found.state


def parse(query: str, companies: dict[str, tuple[str, str]]) -> Intent:
    """What `query` is after. `companies`: {normalized name: (company_key,
    display name)} of the companies with open jobs."""
    role, words = _take_role(geo.search_key(query).split())
    remote, words = _take_remote(words)
    rest = " ".join(w for w in words if w not in FILLER_WORDS)
    area = _place(rest) if rest else None
    location = REMOTE_SLUG if remote else (area.slug if area else None)

    if role is not None:
        values = {"role": role, "location": location} if location else {"role": role}
        key = job_collections.collection_key(values)
        return Intent("role", key, collection_admin.role_name(role, location), key, role=role, location=location)
    if rest and not remote and rest in companies:  # before a place: "acme" is a company and a town
        company_key, name = companies[rest]
        return Intent("company", f"company:{company_key}", f"{name} jobs", company=company_key)
    if location is not None and (area is not None or not rest):
        label = "Remote jobs" if remote else f"Jobs in {area.name}"
        return Intent("location", f"location:{location}", label, location=location)
    text = geo.search_key(query)
    return Intent("other", f"query:{text}", text)


class _JobCounts:
    """Open jobs by role, role + place, company and place, from the
    generator's live index."""

    def __init__(self, live: list[LinkJob]) -> None:
        self.role: Counter[tuple[str, str | None]] = Counter()
        self.company: Counter[str] = Counter()
        self.area: Counter[str] = Counter()
        self.companies: dict[str, tuple[str, str]] = {}
        for job in live:
            if job.closed:
                continue
            remote = job.workplace_type == WorkplaceType.REMOTE
            places = [m.slug for code in job.metros if (m := geo.metro_by_code(code)) is not None]
            places += [REMOTE_SLUG] if remote else []
            self.area.update(places)
            for title in job.standard_titles:
                role = slugify(title)
                self.role[(role, None)] += 1
                self.role.update((role, place) for place in places)
            if job.company_key:
                self.company[job.company_key] += 1
                name = job.company_display
                self.companies.setdefault(geo.search_key(name), (job.company_key, name))

    def open_jobs(self, intent: Intent) -> int | None:
        if intent.kind == "role":
            return self.role[(intent.role, intent.location)]
        if intent.kind == "location":
            return self.area[intent.location]
        if intent.kind == "company":
            return self.company[intent.company]
        return None


def build(rows: list[dict], live: list[LinkJob], now: datetime) -> list[SearchOpportunity]:
    """One SearchOpportunity per intent in `rows` (Search Console's (query,
    page) rows), most shown first."""
    counts = _JobCounts(live)
    intents: dict[str, Intent] = {}
    totals: dict[str, list[float]] = defaultdict(lambda: [0, 0, 0.0])
    queries: dict[str, Counter] = defaultdict(Counter)
    query_clicks: dict[str, Counter] = defaultdict(Counter)
    pages: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        query, url = row["keys"]
        if (path := page_path(url)) is None:
            continue
        intent = parse(query, counts.companies)
        intents.setdefault(intent.key, intent)
        clicks, impressions = row.get("clicks", 0), row.get("impressions", 0)
        t = totals[intent.key]
        t[0] += clicks
        t[1] += impressions
        t[2] += row.get("position", 0) * impressions
        queries[intent.key][query] += impressions
        query_clicks[intent.key][query] += clicks
        pages[intent.key]["/" + path] += impressions

    today = eastern(now).date()
    out = [
        SearchOpportunity(
            key=key, kind=intent.kind, label=intent.label, collection_key=intent.collection_key,
            open_jobs=counts.open_jobs(intent), clicks=int(clicks), impressions=int(impressions),
            position=round(weighted / impressions, 2) if impressions else 0.0,
            queries=[
                {"query": q, "clicks": int(query_clicks[key][q]), "impressions": int(n)}
                for q, n in queries[key].most_common(TOP_QUERIES)
            ],
            pages=[{"page": p, "impressions": int(n)} for p, n in pages[key].most_common(TOP_PAGES)],
            updated_on=today,
        )
        for key, intent in intents.items()
        for clicks, impressions, weighted in [totals[key]]
    ]
    out.sort(key=lambda o: (-o.impressions, -o.clicks, o.key))
    return out


def store(db: Session, opportunities: list[SearchOpportunity]) -> None:
    """Replace what's stored with `opportunities`. Commits."""
    db.execute(delete(SearchOpportunity))
    db.add_all(opportunities)
    db.commit()


def demand(opportunities: list[SearchOpportunity]) -> dict[str, int]:
    """{collection key: impressions} of the role searches, for discovery."""
    return {o.collection_key: o.impressions for o in opportunities if o.collection_key}
