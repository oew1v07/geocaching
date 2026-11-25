"""Download boundary files for every country in the database, then assign counties.

  * UK: OS Open Boundary-Line ceremonial counties (England), preserved counties (Wales) and
    lieutenancy areas (Scotland), via Esri UK's Living Atlas feature service.
    Contains OS data (c) Crown copyright and database right; Open Government Licence v3.0.
  * Elsewhere: GADM 4.1, level 2 (counties / equivalents) where available, otherwise level 1.
    GADM data is free for non-commercial use: https://gadm.org/license.html
  * Country outlines for the HTML map: Natural Earth 1:50m admin-0 countries (public domain).

Files go in ./boundaries and are only downloaded once.

Usage:  python fetch_boundaries.py [--recompute]
"""
import argparse
import io
import json
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from shapely.geometry import mapping, shape

from counties import BOUNDARY_DIR, COUNTRY_ISO3, CUSTOM_SOURCES, WORLD_FILE, assign_counties, boundary_file
from db import connect

URL = "https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_{iso3}_{level}.json.zip"
UK_LAYER = ("https://services-eu1.arcgis.com/T8qQXUocQr5Q2AWw/arcgis/rest/services/"
            "OS_Boundary_Supplementary_View/FeatureServer/1/query")
UK_SIMPLIFY_DEG = 0.0001  # ~10 m; full-resolution polygons are ~1 MB each
WORLD_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
             "ne_50m_admin_0_countries.geojson")


def download_world():
    """Country outlines for the map background; keeps only the name to stay small."""
    with urllib.request.urlopen(WORLD_URL, timeout=300) as resp:
        fc = json.load(resp)
    features = [{"type": "Feature", "properties": {"name": f["properties"].get("NAME")}, "geometry": f["geometry"]}
                for f in fc["features"]]
    BOUNDARY_DIR.mkdir(exist_ok=True)
    WORLD_FILE.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    print(f"world: saved {len(features)} country outlines")


def _get_json(url, params):
    with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=300) as resp:
        return json.load(resp)


def download_uk():
    """Fetch the 91 UK ceremonial/preserved/lieutenancy areas one at a time and simplify locally.
    (The service returns null geometry if asked to generalise server-side.)"""
    ids = _get_json(UK_LAYER, {"where": "1=1", "returnIdsOnly": "true", "f": "json"})["objectIds"]
    features = []
    for n, oid in enumerate(sorted(ids), 1):
        fc = _get_json(UK_LAYER, {"objectIds": oid, "outFields": "osid,name1_text,description",
                                  "outSR": 4326, "geometryPrecision": 6, "f": "geojson"})
        for feat in fc["features"]:
            geom = shape(feat["geometry"]).simplify(UK_SIMPLIFY_DEG, preserve_topology=True)
            features.append({"type": "Feature", "properties": feat["properties"], "geometry": mapping(geom)})
        print(f"\rGBR: {n}/{len(ids)} areas", end="", flush=True)
    BOUNDARY_DIR.mkdir(exist_ok=True)
    (BOUNDARY_DIR / CUSTOM_SOURCES["GBR"]["file"]).write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    print(f"\nGBR: saved {len(features)} areas")


def download(iso3):
    if iso3 == "GBR":
        return download_uk()
    BOUNDARY_DIR.mkdir(exist_ok=True)
    for level in (2, 1):
        url = URL.format(iso3=iso3, level=level)
        try:
            with urllib.request.urlopen(url, timeout=300) as resp:
                data = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            raise
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            name = f"gadm41_{iso3}_{level}.json"
            (BOUNDARY_DIR / name).write_bytes(zf.read(name))
        print(f"{iso3}: downloaded level {level}")
        return
    print(f"{iso3}: no GADM level 1/2 file available")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recompute", action="store_true", help="re-assign counties for all caches, not just unassigned")
    args = ap.parse_args()
    conn = connect()
    if not WORLD_FILE.exists():
        download_world()
    countries = [r[0] for r in conn.execute("SELECT DISTINCT country FROM caches WHERE country IS NOT NULL")]
    for country in countries:
        iso3 = COUNTRY_ISO3.get(country)
        if iso3 is None:
            print(f"{country}: no ISO3 code known; add it to COUNTRY_ISO3 in counties.py")
        elif boundary_file(iso3)[0] is None:
            download(iso3)
    assign_counties(conn, recompute=args.recompute)


if __name__ == "__main__":
    main()
