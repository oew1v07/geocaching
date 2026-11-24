"""Add a "D/T still to do" named filter to a copy of a c:geo backup.

c:geo has no text import for filters; named filters live in its database (table cg_filters).
This script never edits your backup: it copies the whole backup folder to a new folder,
adds (or replaces) the named filter in the copied database, and you restore that copy in c:geo
(Settings > Backup / Restore).

The filter is c:geo's "Difficulty/Terrain matrix" filter with every D/T combination you have
not found yet (from geocaching.db) ticked. Caches without a D/T (e.g. lab caches) are excluded.

Only c:geo databases at version 107 or newer are supported (the cg_filters format changed then).

Usage:  python cgeo_filter.py path/to/cgeo-backup-folder [-o output-folder] [--name "D/T still to do"]
"""
import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

from db import connect
from stats import DT_VALUES

MIN_CGEO_DB_VERSION = 107  # cg_filters stores NamedFilter JSON from this version on


def missing_combis():
    conn = connect()
    done = {(r[0], r[1]) for r in conn.execute(
        "SELECT DISTINCT difficulty, terrain FROM caches WHERE found_date IS NOT NULL AND type != 'Lab Cache'")}
    return [f"{d:.1f}-{t:.1f}" for d in DT_VALUES for t in DT_VALUES if (d, t) not in done]


def find_cgeo_db(folder):
    candidates = [p for p in folder.rglob("*") if p.is_file() and p.suffix in (".sqlite", ".db", "")]
    dbs = []
    for p in candidates:
        try:
            with open(p, "rb") as f:
                if f.read(16) != b"SQLite format 3\x00":
                    continue
            c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cg_filters'").fetchone():
                dbs.append(p)
            c.close()
        except (OSError, sqlite3.Error):
            continue
    if len(dbs) != 1:
        sys.exit(f"expected exactly one c:geo database (with a cg_filters table) in {folder}, found {len(dbs)}: "
                 + ", ".join(map(str, dbs)))
    return dbs[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("backup", type=Path, help="c:geo backup folder (copied, never modified)")
    ap.add_argument("-o", "--output", type=Path, help="where to write the patched copy (default: <backup>-dt-filter)")
    ap.add_argument("--name", default="D/T still to do", help="name of the c:geo filter")
    args = ap.parse_args()

    src = args.backup.resolve()
    if not src.is_dir():
        sys.exit(f"{src} is not a folder")
    out = (args.output or src.with_name(src.name + "-dt-filter")).resolve()
    if out.exists():
        sys.exit(f"{out} already exists; remove it or pass -o")

    combis = missing_combis()
    if not combis:
        sys.exit("No D/T combinations left to do - nothing to add.")

    # Check the original before copying anything
    src_db = find_cgeo_db(src)
    ro = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    version = ro.execute("PRAGMA user_version").fetchone()[0]
    cols = [r[1] for r in ro.execute("PRAGMA table_info(cg_filters)")]
    ro.close()
    if version < MIN_CGEO_DB_VERSION or "priority" not in cols:
        sys.exit(f"c:geo database version {version} (cg_filters columns {cols}) is older than "
                 f"{MIN_CGEO_DB_VERSION}; update c:geo, make a new backup and try again.")

    shutil.copytree(src, out)
    db_path = out / src_db.relative_to(src)
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute("DELETE FROM cg_filters WHERE name = ?", (args.name,))
        cur = conn.execute("INSERT INTO cg_filters (name, treeconfig) VALUES (?, '')", (args.name,))
        row_id = cur.lastrowid
        named_filter = {
            "id": row_id,
            "name": args.name,
            "filter": {
                "advanced": True,
                "inconclusive": False,
                "tree": {
                    "type": "difficulty_terrain_matrix",
                    "config": {"combis": combis, "include-wo-dt": False},
                },
            },
        }
        conn.execute("UPDATE cg_filters SET treeconfig = ? WHERE _id = ?",
                     (json.dumps(named_filter, separators=(",", ":")), row_id))
    conn.close()

    print(f"c:geo database version {version}: added filter '{args.name}' with {len(combis)} D/T combinations")
    print(f"patched backup written to {out}")
    print("Restore that folder in c:geo (Settings > Backup / Restore). Your original backup is untouched.")


if __name__ == "__main__":
    main()
