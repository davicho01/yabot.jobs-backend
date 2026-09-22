"""One-off: regenerate the bundled geography data in app/data/geo/.

app.services.geo resolves a posting's location strings to a US Census
metropolitan/micropolitan area (CBSA) entirely from these committed files —
nothing is downloaded at runtime. Re-run this only to refresh them (new Census
delineation, updated GeoNames populations); review the diff before committing.

Sources:
  - GeoNames cities1000 / cities15000 (https://www.geonames.org, CC BY 4.0 —
    attribution lives in app/data/geo/NOTICE.md and the site footer).
  - Census CBSA delineation via the NBER county crosswalk (public domain):
    the 2020 vintage everywhere except Connecticut, which comes from 2023. The
    2023 delineation moved Connecticut from counties to planning regions, and
    GeoNames' Connecticut codes are the planning regions (09110..09190) — using
    2020 there would leave every Connecticut place without a metro.
  - GeoNames Puerto Rico is country "PR" with the municipio FIPS in admin1.

Outputs:
  us_states.csv          abbr, name, fips
  us_cbsa_counties.csv   county_fips, cbsa_code, cbsa_title, kind    (only counties inside a CBSA)
  us_places.tsv          name, ascii, state, county_fips, population, aliases, lat, lon
                         (lat/lon centre a radius search on a city; see geo.nearby_area_codes)
  world_city_guard.tsv   name, country, population   (non-US cities >= 100k; lets the resolver
                         refuse to read a bare "London"/"Paris" as a small US namesake)
  world_countries.tsv    name, iso2, iso3   (non-US countries; lets the resolver refuse to read a
                         two-letter code as a US state when the entry names another country —
                         "IN - Hyderabad, India" is not Indiana)

Usage:
    python -m one_off.build_geo_data                    # download (cached) and rebuild
    python -m one_off.build_geo_data --cache-dir /tmp/geo
"""

import argparse
import csv
import io
import tempfile
import urllib.request
import zipfile
from pathlib import Path

OUT_DIR = Path(__file__).parent.parent / "app" / "data" / "geo"

CITIES1000_URL = "https://download.geonames.org/export/dump/cities1000.zip"
CITIES15000_URL = "https://download.geonames.org/export/dump/cities15000.zip"
COUNTRY_INFO_URL = "https://download.geonames.org/export/dump/countryInfo.txt"
CBSA_2020_URL = "https://data.nber.org/cbsa-csa-fips-county-crosswalk/2020/cbsa2fipsxw_2020.csv"
CBSA_2023_URL = "https://data.nber.org/cbsa-csa-fips-county-crosswalk/2023/cbsa2fipsxw_2023.csv"
CONNECTICUT_FIPS = "09"

# GeoNames' tab-separated "geoname" table.
GEONAMES_COLUMNS = (
    "geonameid name asciiname alternatenames lat lon fclass fcode country cc2 admin1 admin2 admin3 admin4 "
    "population elevation dem timezone modified"
).split()

WORLD_GUARD_MIN_POPULATION = 100_000
# GeoNames alternate names are every spelling in every language; only short-ish
# ASCII ones are useful as English aliases ("New York" for New York City), and
# 3-letter ones are mostly airport codes ("SEA", "DTA") that would misfire.
ALIAS_MIN_LENGTH = 4
ALIAS_MAX_PER_PLACE = 6


def fetch(url: str, cache_dir: Path) -> bytes:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / url.rsplit("/", 1)[1]
    if not cached.exists():
        print(f"downloading {url}")
        with urllib.request.urlopen(url, timeout=120) as response:
            cached.write_bytes(response.read())
    return cached.read_bytes()


def geonames_rows(zip_bytes: bytes):
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".txt") and "readme" not in n.lower())
        with archive.open(name) as raw:
            for line in io.TextIOWrapper(raw, encoding="utf-8"):
                yield dict(zip(GEONAMES_COLUMNS, line.rstrip("\n").split("\t")))


def _cbsa_rows(csv_text: str) -> list[dict]:
    return [r for r in csv.DictReader(io.StringIO(csv_text))]


def build_cbsa(text_2020: str, text_2023: str) -> tuple[list[dict], list[dict]]:
    """(state rows, county->CBSA rows). The source lists every county, with a
    blank CBSA on the ones outside any metro/micro area — those are dropped (a
    place in such a county simply has no area). Connecticut's rows come from the
    2023 vintage (see the module docstring); titles prefer the 2020 wording for a
    CBSA that exists in both."""
    rows_2020 = _cbsa_rows(text_2020)
    titles_2020 = {r["cbsacode"]: r["cbsatitle"] for r in rows_2020 if r["cbsacode"]}
    rows = [r for r in rows_2020 if not r["county_fips"].startswith(CONNECTICUT_FIPS)]
    rows += [r for r in _cbsa_rows(text_2023) if r["county_fips"].startswith(CONNECTICUT_FIPS)]

    states: dict[str, dict] = {}
    counties: dict[str, dict] = {}
    for row in rows_2020 + rows:
        county_fips = row["county_fips"]
        if row["state_abbreviation"] and row["statename"]:
            states[row["state_abbreviation"]] = {
                "abbr": row["state_abbreviation"],
                "name": row["statename"],
                "fips": county_fips[:2],
            }
    for row in rows:
        if row["county_in_cbsa"] == "1" and row["cbsacode"]:
            kind = "metro" if row["metropolitanmicropolitanstatis"].startswith("Metropolitan") else "micro"
            counties[row["county_fips"]] = {
                "county_fips": row["county_fips"],
                "cbsa_code": row["cbsacode"],
                "cbsa_title": titles_2020.get(row["cbsacode"], row["cbsatitle"]),
                "kind": kind,
            }
    return sorted(states.values(), key=lambda r: r["abbr"]), sorted(counties.values(), key=lambda r: r["county_fips"])


def _nearest_county(row: dict, counties: list[tuple[float, float, str]]) -> str | None:
    """County FIPS of the closest place (same state) that has one. Used for the
    handful of places GeoNames gives no county — mostly cities that span several
    (New York City's five boroughs, DC's neighborhoods). Any nearby county is
    fine for our purpose: they all sit in the same metro area."""
    lat, lon = float(row["lat"]), float(row["lon"])
    best, best_distance = None, None
    for other_lat, other_lon, fips in counties:
        distance = (other_lat - lat) ** 2 + ((other_lon - lon) * 0.75) ** 2  # rough; longitude shrinks with latitude
        if best_distance is None or distance < best_distance:
            best, best_distance = fips, distance
    return best


def build_places(zip_bytes: bytes, state_fips: dict[str, str]) -> list[dict]:
    rows = []
    for row in geonames_rows(zip_bytes):
        if row["fclass"] != "P":
            continue
        if row["country"] == "PR" and row["admin1"]:
            # Puerto Rico: the municipio FIPS is in admin1 (admin2 is a GeoNames id).
            row["admin2"], row["admin1"] = row["admin1"], "PR"
        if row["country"] in ("US", "PR") and row["admin1"] in state_fips:
            rows.append(row)
    counties_by_state: dict[str, list[tuple[float, float, str]]] = {}
    for row in rows:
        if row["admin2"]:
            fips = state_fips[row["admin1"]] + row["admin2"].zfill(3)
            counties_by_state.setdefault(row["admin1"], []).append((float(row["lat"]), float(row["lon"]), fips))

    places, inferred = [], []
    for row in rows:
        if row["admin2"]:
            county_fips = state_fips[row["admin1"]] + row["admin2"].zfill(3)
        else:
            county_fips = _nearest_county(row, counties_by_state.get(row["admin1"], []))
            if county_fips is None:
                continue
            inferred.append(f"{row['name']}, {row['admin1']} -> {county_fips}")
        known = {row["name"].lower(), row["asciiname"].lower()}
        aliases = []
        for alt in row["alternatenames"].split(","):
            alt = alt.strip()
            if len(alt) >= ALIAS_MIN_LENGTH and alt.isascii() and alt.lower() not in known and alt not in aliases:
                aliases.append(alt)
        # Big cities have hundreds of alternates in arbitrary order, so keep the
        # ones that overlap the primary name first: "New York" for "New York
        # City", "Salt Lake" for "Salt Lake City", "Sandy City" for "Sandy".
        primary = row["asciiname"].lower()
        aliases.sort(key=lambda a: not (a.lower() in primary or primary in a.lower()))
        places.append(
            {
                "name": row["name"],
                "ascii": row["asciiname"],
                "state": row["admin1"],
                "county_fips": county_fips,
                "population": int(row["population"] or 0),
                "aliases": "|".join(aliases[:ALIAS_MAX_PER_PLACE]),
                "lat": f"{float(row['lat']):.4f}",
                "lon": f"{float(row['lon']):.4f}",
            }
        )
    print(f"inferred a county for {len(inferred)} places with none in GeoNames: {', '.join(inferred[:6])}, ...")
    return sorted(places, key=lambda r: (r["state"], r["ascii"], -r["population"]))


def build_world_guard(zip_bytes: bytes) -> list[dict]:
    guard = []
    for row in geonames_rows(zip_bytes):
        population = int(row["population"] or 0)
        if row["country"] != "US" and population >= WORLD_GUARD_MIN_POPULATION:
            guard.append({"name": row["asciiname"], "country": row["country"], "population": population})
    return sorted(guard, key=lambda r: (r["name"], -r["population"]))


# Names people write that GeoNames' official country names don't cover.
EXTRA_COUNTRY_NAMES = [
    ("UK", "GB", "GBR"), ("Great Britain", "GB", "GBR"), ("England", "GB", "GBR"), ("Scotland", "GB", "GBR"),
    ("Wales", "GB", "GBR"), ("Northern Ireland", "GB", "GBR"), ("UAE", "AE", "ARE"), ("Korea", "KR", "KOR"),
    ("Czech Republic", "CZ", "CZE"), ("Turkiye", "TR", "TUR"), ("Holland", "NL", "NLD"), ("Viet Nam", "VN", "VNM"),
]
# Country names that are also US state names — the resolver treats those as states.
US_STATE_NAMED_COUNTRIES = {"Georgia"}


def build_countries(text: str) -> list[dict]:
    """Non-US countries from GeoNames' countryInfo.txt (tab-separated, `#` comments)."""
    countries = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        cells = line.split("\t")
        iso2, iso3, name = cells[0], cells[1], cells[4]
        if iso2 == "US" or name in US_STATE_NAMED_COUNTRIES:
            continue
        countries.append({"name": name, "iso2": iso2, "iso3": iso3})
    countries += [{"name": n, "iso2": a, "iso3": b} for n, a, b in EXTRA_COUNTRY_NAMES]
    return sorted(countries, key=lambda r: r["name"])


def write(path: Path, rows: list[dict], fieldnames: list[str], delimiter: str) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=delimiter, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {path.relative_to(Path(__file__).parent.parent)}  ({len(rows)} rows, {path.stat().st_size / 1024:.0f} KB)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache-dir", type=Path, default=Path(tempfile.gettempdir()) / "yabot-geo-cache")
    args = parser.parse_args()

    states, counties = build_cbsa(
        fetch(CBSA_2020_URL, args.cache_dir).decode("utf-8"), fetch(CBSA_2023_URL, args.cache_dir).decode("utf-8")
    )
    state_fips = {s["abbr"]: s["fips"] for s in states}
    places = build_places(fetch(CITIES1000_URL, args.cache_dir), state_fips)
    guard = build_world_guard(fetch(CITIES15000_URL, args.cache_dir))
    countries = build_countries(fetch(COUNTRY_INFO_URL, args.cache_dir).decode("utf-8"))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write(OUT_DIR / "us_states.csv", states, ["abbr", "name", "fips"], ",")
    write(OUT_DIR / "us_cbsa_counties.csv", counties, ["county_fips", "cbsa_code", "cbsa_title", "kind"], ",")
    write(OUT_DIR / "us_places.tsv", places, ["name", "ascii", "state", "county_fips", "population", "aliases", "lat", "lon"], "\t")
    write(OUT_DIR / "world_city_guard.tsv", guard, ["name", "country", "population"], "\t")
    write(OUT_DIR / "world_countries.tsv", countries, ["name", "iso2", "iso3"], "\t")


if __name__ == "__main__":
    main()
