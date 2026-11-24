"""County lookup from GADM boundary files stored in ./boundaries (see fetch_boundaries.py)."""
import json
from functools import lru_cache
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

BOUNDARY_DIR = Path(__file__).with_name("boundaries")

# geocaching.com country name -> ISO3 code used by GADM. Extend as needed.
COUNTRY_ISO3 = {
    "United Kingdom": "GBR",
    "Ireland": "IRL",
    "United States": "USA",
    "Canada": "CAN",
    "Mexico": "MEX",
    "France": "FRA",
    "Germany": "DEU",
    "Spain": "ESP",
    "Netherlands": "NLD",
    "Belgium": "BEL",
    "Denmark": "DNK",
    "New Zealand": "NZL",
    "Slovakia": "SVK",
    "Hungary": "HUN",
    "Australia": "AUS",
}


# Countries that use a non-GADM source. GADM 4.1's UK level-2 data is broken (many areas named "NA"),
# so the UK uses OS Open Boundary-Line ceremonial counties (England), preserved counties (Wales)
# and lieutenancy areas (Scotland). Northern Ireland is not covered by that dataset.
CUSTOM_SOURCES = {
    "GBR": {"file": "uk_ceremonial_counties.geojson", "gid": "osid", "name": "name1_text", "region": "description"},
}


def boundary_file(iso3):
    """Return (path, level) of the downloaded boundary file for a country, or (None, None).

    level is "custom" for CUSTOM_SOURCES, else the GADM level (2 preferred, else 1)."""
    if iso3 in CUSTOM_SOURCES:
        p = BOUNDARY_DIR / CUSTOM_SOURCES[iso3]["file"]
        return (p, "custom") if p.exists() else (None, None)
    for level in (2, 1):
        p = BOUNDARY_DIR / f"gadm41_{iso3}_{level}.json"
        if p.exists():
            return p, level
    return None, None


@lru_cache(maxsize=None)
def load_country(iso3):
    """Return (features, geometries, STRtree) for a country, or None if not downloaded."""
    path, level = boundary_file(iso3)
    if path is None:
        return None
    with open(path, encoding="utf-8") as f:
        fc = json.load(f)
    if level == "custom":
        src = CUSTOM_SOURCES[iso3]
        keys = {"gid": src["gid"], "county": src["name"], "region": src["region"]}
    else:
        keys = {"gid": f"GID_{level}", "county": f"NAME_{level}", "region": "NAME_1" if level == 2 else None}
    feats, geoms = [], []
    for feat in fc["features"]:
        p = feat["properties"]
        feats.append({k: p.get(v) if v else None for k, v in keys.items()} | {"iso3": iso3})
        geoms.append(shape(feat["geometry"]))
    return feats, geoms, STRtree(geoms)


def lookup(iso3, lat, lon):
    data = load_country(iso3)
    if data is None:
        return None
    feats, geoms, tree = data
    pt = Point(lon, lat)
    for i in tree.query(pt, predicate="intersects"):
        return feats[int(i)]
    return None


def assign_counties(conn, recompute=False):
    """Fill caches.county / county_gid from boundary files. Returns (#assigned, #unmatched, missing countries)."""
    where = "" if recompute else "WHERE county IS NULL"
    rows = conn.execute(f"SELECT code, country, lat, lon FROM caches {where}").fetchall()
    assigned, unmatched, missing = 0, [], set()
    with conn:
        for r in rows:
            iso3 = COUNTRY_ISO3.get(r["country"])
            if iso3 is None or boundary_file(iso3)[0] is None:
                missing.add(r["country"])
                continue
            hit = lookup(iso3, r["lat"], r["lon"])
            if hit is None:
                unmatched.append(r["code"])
                continue
            conn.execute("UPDATE caches SET county = ?, county_gid = ? WHERE code = ?",
                         (hit["county"], hit["gid"], r["code"]))
            assigned += 1
    if assigned or unmatched or missing:
        print(f"counties: {assigned} assigned", end="")
        if unmatched:
            print(f", {len(unmatched)} outside all boundaries ({', '.join(unmatched)})", end="")
        if missing:
            print(f"; no boundary file for: {', '.join(sorted(m or '?' for m in missing))} "
                  "(run fetch_boundaries.py)", end="")
        print()
    return assigned, unmatched, missing
