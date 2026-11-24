"""Ingest a geocaching.com "My Finds" pocket query (.zip or .gpx) into geocaching.db.

Each PQ is a full snapshot, so:
  * caches in the PQ are inserted or updated;
  * caches already in the DB but missing from this PQ are kept and flagged
    deletion_attempted = 1 (recording the import that dropped them);
  * a flagged cache that reappears in a later PQ has its flag cleared.
Every add / flag / restore is recorded in cache_events.

Usage:  python ingest.py path/to/24785452.zip [more.zip ...]
"""
import argparse
import hashlib
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from db import FIND_LOG_TYPES, connect

GPX_NS = "http://www.topografix.com/GPX/1/0"
# Groundspeak has used both namespace versions in PQs.
GS_NAMESPACES = ("http://www.groundspeak.com/cache/1/0", "http://www.groundspeak.com/cache/1/0/1")


def read_gpx_files(path):
    """Yield (name, bytes) for each .gpx in a zip, or the file itself."""
    path = Path(path)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                # Skip the *-wpts.gpx companion file a PQ can include (child waypoints only).
                if info.filename.lower().endswith(".gpx") and not info.filename.lower().endswith("-wpts.gpx"):
                    yield info.filename, zf.read(info)
    else:
        yield path.name, path.read_bytes()


def _text(el, tag, ns):
    return el.findtext(f"{{{ns}}}{tag}") if el is not None else None


def _float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_gpx(data):
    root = ET.fromstring(data)
    header = {"name": root.findtext(f"{{{GPX_NS}}}name"), "time": root.findtext(f"{{{GPX_NS}}}time")}
    caches = []
    for wpt in root.findall(f"{{{GPX_NS}}}wpt"):
        cache_el, ns = None, None
        for candidate in GS_NAMESPACES:
            cache_el = wpt.find(f"{{{candidate}}}cache")
            if cache_el is not None:
                ns = candidate
                break
        if cache_el is None:
            continue  # additional waypoint, not a cache
        owner_el = cache_el.find(f"{{{ns}}}owner")
        logs = []
        for log in cache_el.findall(f"{{{ns}}}logs/{{{ns}}}log"):
            raw = _text(log, "date", ns) or ""
            logs.append({
                "log_id": log.get("id"),
                # PQ log times are noon Pacific (19:00Z/20:00Z), so the date part is the log date.
                "date": raw[:10] or None,
                "raw_date": raw,
                "type": _text(log, "type", ns),
                "finder": _text(log, "finder", ns),
                "text": _text(log, "text", ns),
            })
        find_dates = [l["date"] for l in logs if l["type"] in FIND_LOG_TYPES and l["date"]]
        placed = wpt.findtext(f"{{{GPX_NS}}}time")
        caches.append({
            "code": wpt.findtext(f"{{{GPX_NS}}}name"),
            "gc_id": cache_el.get("id"),
            "name": _text(cache_el, "name", ns),
            "type": _text(cache_el, "type", ns),
            "container": _text(cache_el, "container", ns),
            "difficulty": _float(_text(cache_el, "difficulty", ns)),
            "terrain": _float(_text(cache_el, "terrain", ns)),
            "lat": _float(wpt.get("lat")),
            "lon": _float(wpt.get("lon")),
            "country": _text(cache_el, "country", ns),
            "state": _text(cache_el, "state", ns),
            "owner": owner_el.text if owner_el is not None else None,
            "placed_by": _text(cache_el, "placed_by", ns),
            "placed_date": placed[:10] if placed else None,
            "found_date": min(find_dates) if find_dates else None,
            "archived": 1 if cache_el.get("archived") == "True" else 0,
            "available": 1 if cache_el.get("available") == "True" else 0,
            "url": wpt.findtext(f"{{{GPX_NS}}}url"),
            "logs": logs,
        })
    return header, caches


CACHE_FIELDS = ("gc_id", "name", "type", "container", "difficulty", "terrain", "lat", "lon", "country",
                "state", "owner", "placed_by", "placed_date", "found_date", "archived", "available", "url")


def ingest(conn, source_path):
    raw = Path(source_path).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if conn.execute("SELECT 1 FROM imports WHERE sha256 = ?", (sha,)).fetchone():
        print(f"{source_path}: already imported, skipping")
        return

    header, caches = {}, {}
    for name, data in read_gpx_files(source_path):
        h, cs = parse_gpx(data)
        header = header or h
        for c in cs:
            caches[c["code"]] = c
    if not caches:
        sys.exit(f"{source_path}: no caches found in file")

    latest = conn.execute("SELECT MAX(gpx_time) FROM imports").fetchone()[0]
    if latest and header.get("time") and header["time"] < latest:
        sys.exit(f"{source_path}: PQ generated {header['time']} is older than the latest import ({latest}); "
                 "importing it would wrongly flag newer caches. Refusing.")

    with conn:
        cur = conn.execute(
            "INSERT INTO imports (source_file, gpx_name, gpx_time, sha256, imported_at, n_caches) VALUES (?,?,?,?,?,?)",
            (str(source_path), header.get("name"), header.get("time"), sha,
             datetime.now(timezone.utc).isoformat(timespec="seconds"), len(caches)))
        imp = cur.lastrowid
        existing = {r["code"]: r["deletion_attempted"] for r in conn.execute("SELECT code, deletion_attempted FROM caches")}
        n_new = n_restored = 0

        for code, c in caches.items():
            values = [c[f] for f in CACHE_FIELDS]
            if code not in existing:
                conn.execute(
                    f"INSERT INTO caches (code, {', '.join(CACHE_FIELDS)}, first_seen_import, last_seen_import) "
                    f"VALUES ({', '.join('?' * (len(CACHE_FIELDS) + 3))})",
                    [code, *values, imp, imp])
                conn.execute("INSERT INTO cache_events (code, import_id, event) VALUES (?,?,'added')", (code, imp))
                n_new += 1
            else:
                # The PQ only has the latest logs, so keep the earliest find date we've ever seen.
                conn.execute(
                    f"UPDATE caches SET {', '.join(f'{f} = ?' for f in CACHE_FIELDS)}, last_seen_import = ?, "
                    "deletion_attempted = 0, deletion_attempted_import = NULL WHERE code = ?",
                    [*values, imp, code])
                if existing[code]:
                    conn.execute("INSERT INTO cache_events (code, import_id, event) VALUES (?,?,'restored')", (code, imp))
                    n_restored += 1
            for log in c["logs"]:
                conn.execute(
                    "INSERT INTO logs (log_id, code, date, raw_date, type, finder, text, first_seen_import, last_seen_import) "
                    "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(log_id) DO UPDATE SET date=excluded.date, "
                    "raw_date=excluded.raw_date, type=excluded.type, text=excluded.text, last_seen_import=excluded.last_seen_import",
                    (log["log_id"], code, log["date"], log["raw_date"], log["type"], log["finder"], log["text"], imp, imp))

        # The PQ may drop older logs, so use the earliest find log ever stored for each cache.
        placeholders = ", ".join("?" * len(FIND_LOG_TYPES))
        conn.execute(
            f"UPDATE caches SET found_date = COALESCE((SELECT MIN(date) FROM logs WHERE logs.code = caches.code "
            f"AND logs.type IN ({placeholders})), found_date) WHERE last_seen_import = ?",
            [*FIND_LOG_TYPES, imp])

        missing = [code for code, flagged in existing.items() if code not in caches and not flagged]
        for code in missing:
            conn.execute("UPDATE caches SET deletion_attempted = 1, deletion_attempted_import = ? WHERE code = ?", (imp, code))
            conn.execute("INSERT INTO cache_events (code, import_id, event) VALUES (?,?,'deletion_attempted')", (code, imp))

        conn.execute("UPDATE imports SET n_new = ?, n_flagged = ?, n_restored = ? WHERE id = ?",
                     (n_new, len(missing), n_restored, imp))

    no_date = [c for c, v in caches.items() if not v["found_date"]]
    print(f"{source_path}: {len(caches)} caches ({n_new} new, {n_restored} restored, "
          f"{len(missing)} flagged as deletion attempted)")
    if no_date:
        print(f"  warning: {len(no_date)} caches have no find log in the PQ: {', '.join(sorted(no_date))}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="pocket query .zip or .gpx files, oldest first")
    args = ap.parse_args()
    conn = connect()
    for f in args.files:
        ingest(conn, f)
    try:
        from counties import assign_counties
        assign_counties(conn)
    except ImportError:
        pass


if __name__ == "__main__":
    main()
