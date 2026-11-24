"""Download the geocaching.com cache type icons once into ./icons (git-ignored).

The icons are Groundspeak's artwork, so they're kept locally for personal use only.
stats.py embeds them in the HTML output; types without an icon fall back to plain text / dots.

Usage:  python fetch_icons.py [--force]
"""
import argparse
import urllib.request
from pathlib import Path

ICON_DIR = Path(__file__).with_name("icons")
ICON_URL = "https://www.geocaching.com/images/WptTypes/{id}.gif"

# Cache type name as it appears in the PQ -> geocaching.com type id
TYPE_ICON_IDS = {
    "Traditional Cache": 2,
    "Multi-cache": 3,
    "Virtual Cache": 4,
    "Letterbox Hybrid": 5,
    "Event Cache": 6,
    "Unknown Cache": 8,
    "Unknown (Mystery) Cache": 8,
    "Project APE Cache": 9,
    "Webcam Cache": 11,
    "Locationless (Reverse) Cache": 12,
    "Cache In Trash Out Event": 13,
    "Earthcache": 137,
    "Mega-Event Cache": 453,
    "GPS Adventures Exhibit": 1304,
    "Wherigo Cache": 1858,
    "Community Celebration Event": 3653,
    "Lost and Found Event Cache": 3653,
    "Groundspeak HQ": 3773,
    "Geocaching HQ": 3773,
    "Geocaching HQ Celebration": 3774,
    "Geocaching HQ Block Party": 4738,
    "Giga-Event Cache": 7005,
}


def icon_path(type_name):
    type_id = TYPE_ICON_IDS.get(type_name)
    if type_id is None:
        return None
    p = ICON_DIR / f"{type_id}.gif"
    return p if p.exists() else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="re-download icons that already exist")
    args = ap.parse_args()
    ICON_DIR.mkdir(exist_ok=True)
    for type_id in sorted(set(TYPE_ICON_IDS.values())):
        dest = ICON_DIR / f"{type_id}.gif"
        if dest.exists() and not args.force:
            continue
        req = urllib.request.Request(ICON_URL.format(id=type_id), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            dest.write_bytes(resp.read())
        print(f"downloaded {dest.name}")
    print(f"icons in {ICON_DIR}")


if __name__ == "__main__":
    main()
