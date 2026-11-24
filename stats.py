"""Geocaching stats from geocaching.db.

Usage:
  python stats.py                       # plain-text tables
  python stats.py --format json
  python stats.py --format html -o output/stats.html
  python stats.py --todo                # also list difficulty/terrain combos still to find

Lab caches are excluded from all stats. Caches flagged as deletion-attempted are still counted.
"""
import argparse
import html
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from itertools import combinations
from pathlib import Path

from db import connect

DT_VALUES = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
EXCLUDED_TYPES = ("Lab Cache",)


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fmt_dt(v):
    return f"{v:g}"


def load_finds(conn):
    q = (f"SELECT * FROM caches WHERE found_date IS NOT NULL "
         f"AND type NOT IN ({', '.join('?' * len(EXCLUDED_TYPES))})")
    return [dict(r) for r in conn.execute(q, EXCLUDED_TYPES)]


def compute(conn, include_todo=False):
    finds = load_finds(conn)

    # Finds per cache type
    types = Counter(f["type"] for f in finds)
    by_type = [{"type": t, "finds": n} for t, n in sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))]

    # Longest consecutive-day streak
    days = sorted({date.fromisoformat(f["found_date"]) for f in finds})
    best = {"days": 0, "start": None, "end": None}
    run_start = prev = None
    for d in days:
        run_start = d if prev is None or d - prev != timedelta(days=1) else run_start
        length = (d - run_start).days + 1
        if length > best["days"]:
            best = {"days": length, "start": run_start.isoformat(), "end": d.isoformat()}
        prev = d

    # Furthest distance in one day = largest straight-line distance between any two finds that day
    per_day = defaultdict(list)
    for f in finds:
        per_day[f["found_date"]].append(f)
    furthest = {"date": None, "km": 0.0, "miles": 0.0, "from": None, "to": None}
    for d, fs in per_day.items():
        for a, b in combinations(fs, 2):
            km = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
            if km > furthest["km"]:
                furthest = {"date": d, "km": round(km, 2), "miles": round(km / 1.609344, 2),
                            "from": f"{a['code']} {a['name']}", "to": f"{b['code']} {b['name']}"}

    # Counties
    county_counts = Counter((f["country"], f["county"], f["county_gid"]) for f in finds)
    counties = [{"country": c, "county": k or "Unknown", "gid": g, "finds": n}
                for (c, k, g), n in sorted(county_counts.items(),
                                           key=lambda kv: (kv[0][0] or "", kv[0][1] is None, kv[0][1] or ""))]

    # Difficulty / terrain matrix
    dt = Counter((f["difficulty"], f["terrain"]) for f in finds)
    matrix = {fmt_dt(d): {fmt_dt(t): dt.get((d, t), 0) for t in DT_VALUES} for d in DT_VALUES}
    completed = sum(1 for d in DT_VALUES for t in DT_VALUES if dt.get((d, t)))

    result = {
        "total_finds": len(finds),
        "finds_by_type": by_type,
        "longest_streak": best,
        "furthest_day": furthest,
        "counties": counties,
        "dt_matrix": {"rows_difficulty_cols_terrain": matrix, "completed": completed, "of": len(DT_VALUES) ** 2},
        "deletion_attempted": [dict(r) for r in conn.execute(
            "SELECT c.code, c.name, i.gpx_time AS missing_since FROM caches c "
            "JOIN imports i ON i.id = c.deletion_attempted_import WHERE c.deletion_attempted = 1 ORDER BY c.code")],
    }
    if include_todo:
        result["dt_todo"] = [{"difficulty": d, "terrain": t} for d in DT_VALUES for t in DT_VALUES
                             if not dt.get((d, t))]
    return result, finds


# ---------------------------------------------------------------- table output

def text_table(headers, rows):
    rows = [[("" if v is None else str(v)) for v in r] for r in rows]
    widths = [max(len(str(h)), *(len(r[i]) for r in rows)) if rows else len(str(h)) for i, h in enumerate(headers)]
    line = "  ".join("-" * w for w in widths)
    out = ["  ".join(str(h).ljust(w) for h, w in zip(headers, widths)), line]
    out += ["  ".join(v.rjust(w) if v.replace(".", "").isdigit() else v.ljust(w) for v, w in zip(r, widths))
            for r in rows]
    return "\n".join(out)


def render_table(s):
    parts = [f"Total finds: {s['total_finds']}", ""]
    parts += ["Finds by cache type", text_table(["Type", "Finds"], [[r["type"], r["finds"]] for r in s["finds_by_type"]]), ""]
    st = s["longest_streak"]
    parts += [f"Longest streak: {st['days']} days ({st['start']} to {st['end']})", ""]
    fd = s["furthest_day"]
    parts += [f"Furthest distance in one day: {fd['km']} km / {fd['miles']} mi on {fd['date']}",
              f"  {fd['from']}  ->  {fd['to']}", ""]
    parts += ["Counties cached in", text_table(["Country", "County", "Finds"],
                                               [[r["country"], r["county"], r["finds"]] for r in s["counties"]]), ""]
    m = s["dt_matrix"]
    parts += [f"Difficulty (rows) / Terrain (columns): {m['completed']}/{m['of']} combinations completed",
              text_table(["D\\T", *map(fmt_dt, DT_VALUES)],
                         [[d, *(row[t] or "." for t in row)] for d, row in m["rows_difficulty_cols_terrain"].items()]), ""]
    if "dt_todo" in s:
        parts += [f"D/T combinations still to do ({len(s['dt_todo'])})",
                  text_table(["Difficulty", "Terrain"], [[fmt_dt(r["difficulty"]), fmt_dt(r["terrain"])] for r in s["dt_todo"]]), ""]
    if s["deletion_attempted"]:
        parts += ["Caches missing from the latest pocket query (kept, flagged)",
                  text_table(["Code", "Name", "Missing since PQ"],
                             [[r["code"], r["name"], r["missing_since"]] for r in s["deletion_attempted"]]), ""]
    return "\n".join(parts)


# ---------------------------------------------------------------- html output

def county_geojson(counties):
    """GeoJSON of the counties cached in, simplified for embedding."""
    from shapely.geometry import mapping
    from counties import COUNTRY_ISO3, load_country

    wanted = defaultdict(dict)
    for c in counties:
        if c["gid"]:
            wanted[COUNTRY_ISO3.get(c["country"])][c["gid"]] = c
    features = []
    for iso3, gids in wanted.items():
        data = load_country(iso3) if iso3 else None
        if data is None:
            continue
        feats, geoms, _ = data
        for feat, geom in zip(feats, geoms):
            if feat["gid"] in gids:
                c = gids[feat["gid"]]
                features.append({"type": "Feature",
                                 "properties": {"name": c["county"], "country": c["country"], "finds": c["finds"]},
                                 "geometry": mapping(geom.simplify(0.002, preserve_topology=True))})
    return {"type": "FeatureCollection", "features": features}


def icon_data_uris(types):
    """Map cache type -> data: URI of its locally downloaded icon (see fetch_icons.py)."""
    import base64
    from fetch_icons import icon_path

    uris = {}
    for t in types:
        p = icon_path(t)
        if p:
            uris[t] = "data:image/gif;base64," + base64.b64encode(p.read_bytes()).decode("ascii")
    return uris


def h_table(headers, rows, cls=""):
    e = html.escape
    head = "".join(f"<th>{e(str(h))}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{e('' if v is None else str(v))}</td>" for v in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def render_html(s, finds):
    e = html.escape
    m = s["dt_matrix"]
    matrix_rows = ""
    for d, row in m["rows_difficulty_cols_terrain"].items():
        cells = "".join(f'<td class="{"hit" if n else "miss"}">{n or ""}</td>' for n in row.values())
        matrix_rows += f"<tr><th>{d}</th>{cells}</tr>"
    matrix = (f'<table class="matrix"><thead><tr><th>D \\ T</th>{"".join(f"<th>{fmt_dt(t)}</th>" for t in DT_VALUES)}'
              f"</tr></thead><tbody>{matrix_rows}</tbody></table>")
    st, fd = s["longest_streak"], s["furthest_day"]
    todo = ""
    if "dt_todo" in s:
        todo = (f"<h3>Still to do ({len(s['dt_todo'])})</h3>"
                + h_table(["Difficulty", "Terrain"], [[fmt_dt(r["difficulty"]), fmt_dt(r["terrain"])] for r in s["dt_todo"]]))
    flagged = ""
    if s["deletion_attempted"]:
        flagged = ("<section><h2>Missing from latest pocket query</h2>"
                   + h_table(["Code", "Name", "Missing since PQ"],
                             [[r["code"], r["name"], r["missing_since"]] for r in s["deletion_attempted"]]) + "</section>")
    geo = json.dumps(county_geojson(s["counties"]))
    icons = icon_data_uris({f["type"] for f in finds})
    points = json.dumps([[f["lat"], f["lon"], f"{f['code']} {f['name']} ({f['type']})", f["type"]] for f in finds])
    type_rows = "".join(
        f'<tr><td>{f"<img class=ticon src={icons[r["type"]]} alt=>" if r["type"] in icons else ""}{e(r["type"])}</td>'
        f'<td>{r["finds"]}</td></tr>' for r in s["finds_by_type"])
    type_table = f"<table><thead><tr><th>Type</th><th>Finds</th></tr></thead><tbody>{type_rows}</tbody></table>"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Geocaching Stats</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
:root {{ --bg:#fafaf7; --fg:#1d2a1f; --muted:#5f6b60; --line:#d9ddd3; --accent:#2f7d3a; --hit:#cfe8c9; --miss:#f3f3ef; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#141914; --fg:#e6ebe3; --muted:#9aa597; --line:#2e372d; --accent:#7fc47f; --hit:#2f5a31; --miss:#1d231c; }} }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; }}
main {{ max-width:960px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ margin:0 0 4px; }} h2 {{ margin-top:40px; border-bottom:2px solid var(--accent); padding-bottom:4px; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; margin-top:20px; }}
.kpi {{ border:1px solid var(--line); border-radius:8px; padding:12px 16px; }}
.kpi b {{ display:block; font-size:28px; color:var(--accent); }} .kpi span {{ color:var(--muted); font-size:13px; }}
table {{ border-collapse:collapse; margin-top:8px; }} th, td {{ border:1px solid var(--line); padding:4px 10px; text-align:left; }}
td:last-child {{ text-align:right; }}
.matrix td, .matrix th {{ text-align:center; min-width:32px; }} .matrix .hit {{ background:var(--hit); font-weight:600; }}
.matrix .miss {{ background:var(--miss); }}
#map {{ height:520px; border:1px solid var(--line); border-radius:8px; margin-top:8px; }}
.wrap {{ overflow-x:auto; }}
.ticon {{ width:20px; height:20px; vertical-align:middle; margin-right:8px; }}
</style></head><body><main>
<h1>Geocaching Stats</h1>
<div class="kpis">
 <div class="kpi"><b>{s['total_finds']}</b><span>finds (excluding lab caches)</span></div>
 <div class="kpi"><b>{st['days']} days</b><span>longest streak: {e(str(st['start']))} to {e(str(st['end']))}</span></div>
 <div class="kpi"><b>{fd['km']} km</b><span>{fd['miles']} mi furthest in one day ({e(str(fd['date']))})<br>{e(str(fd['from']))} &rarr; {e(str(fd['to']))}</span></div>
 <div class="kpi"><b>{m['completed']}/{m['of']}</b><span>difficulty/terrain combinations</span></div>
</div>
<section><h2>Finds by cache type</h2>{type_table}</section>
<section><h2>Counties</h2><div id="map"></div>
<div class="wrap">{h_table(["Country", "County", "Finds"], [[r["country"], r["county"], r["finds"]] for r in s["counties"]])}</div></section>
<section><h2>Difficulty / terrain matrix</h2><p>Rows are difficulty, columns terrain.</p><div class="wrap">{matrix}</div>{todo}</section>
{flagged}
</main>
<script>
const counties = {geo};
const finds = {points};
const icons = {json.dumps(icons)};
const map = L.map('map');
// OpenTopoMap basemap (OpenStreetMap's own tiles and CARTO both refuse pages opened from disk).
L.tileLayer('https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 17, subdomains: 'abc',
  attribution: 'Map data &copy; OpenStreetMap contributors, SRTM | style &copy; OpenTopoMap (CC-BY-SA); boundaries &copy; GADM; contains OS data &copy; Crown copyright and database right' }}).addTo(map);
const leafletIcons = Object.fromEntries(Object.entries(icons).map(([t, uri]) =>
  [t, L.icon({{ iconUrl: uri, iconSize: [20, 20], iconAnchor: [10, 10], tooltipAnchor: [10, 0] }})]));
const layer = L.geoJSON(counties, {{ style: {{ color:'#2f7d3a', weight:1, fillColor:'#4caf50', fillOpacity:0.45 }},
  onEachFeature: (f, l) => l.bindTooltip(`${{f.properties.name}}, ${{f.properties.country}}: ${{f.properties.finds}} finds`) }});
// The map needs a view before vector layers are added.
const b = layer.getBounds();
if (b.isValid()) map.fitBounds(b); else map.setView([52, -1], 6);
layer.addTo(map);
finds.forEach(([lat, lon, name, type]) => (leafletIcons[type]
  ? L.marker([lat, lon], {{ icon: leafletIcons[type] }})
  : L.circleMarker([lat, lon], {{ radius:4, color:'#1b4d22', weight:1, fillOpacity:0.9 }})).bindTooltip(name).addTo(map));
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--format", choices=["table", "json", "html"], default="table")
    ap.add_argument("--todo", action="store_true", help="list difficulty/terrain combinations not yet found")
    ap.add_argument("-o", "--output", help="write to this file instead of stdout")
    args = ap.parse_args()

    conn = connect()
    stats, finds = compute(conn, include_todo=args.todo)
    if args.format == "json":
        out = json.dumps(stats, indent=2, ensure_ascii=False)
    elif args.format == "html":
        out = render_html(stats, finds)
    else:
        out = render_table(stats)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
