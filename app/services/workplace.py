"""Work type (remote / hybrid / on-site) as stated by a posting's location entries.

Adapters guess a posting's work type from its own fields, and one of their
defaults is that any plain place means on-site — so "Remote - United States"
postings were filed as on-site. When the location text itself says Remote,
Hybrid or Office/HQ, that is better evidence, so it is read here (the words are
found by app.services.geo, which strips them off before looking for the place).
"""

from app.models.enums import WorkplaceType
from app.services.geo import resolve_entry


def infer_workplace_type(entries: list[str]) -> WorkplaceType | None:
    """The work type a posting's location entries state, or None if they don't
    say (or say conflicting things).

    - An entry's own words count: "Hybrid" -> hybrid, "Remote - California" ->
      remote, "New York, NY Office" / "HQ" / "On-site" -> on-site.
    - A real city together with a remote option is hybrid — "Boston or Remote",
      or a city entry alongside "Remote - US" — the same reading JSON-LD postings
      get (a remote posting that also names an on-site location is hybrid).
    - Remote with only a state or country ("Remote - California") is remote.
    - Conflicting entries (remote *and* hybrid, or on-site *and* remote), or
      remote next to a place we don't recognize (it could be a city), say nothing.
    """
    types: set[str] = set()
    plain_city = False
    unrecognized_place = False
    for entry in entries:
        resolution = resolve_entry(entry)
        if resolution.workplace:
            # "Boston or Remote": a city and a remote option in one entry.
            types.add("hybrid" if resolution.workplace == "remote" and resolution.city else resolution.workplace)
        elif resolution.city:
            plain_city = True
        elif resolution.geo == "other":
            unrecognized_place = True

    if "remote" in types and plain_city:
        types.discard("remote")
        types.add("hybrid")
    if not types or (types == {"remote"} and unrecognized_place) or len(types) > 1:
        return None
    return WorkplaceType(types.pop())


def reconcile_workplace_type(current: str, derived: WorkplaceType | None) -> str:
    """Combine the adapter's work type with what the location text states.

    The text replaces the adapter's `unknown`, and its `onsite` — the default
    guess for a plain place, which is exactly what a "Remote - US" posting got —
    but never an adapter's explicit `remote` or `hybrid`."""
    if derived is not None and current in (WorkplaceType.UNKNOWN, WorkplaceType.ONSITE):
        return derived
    return current
