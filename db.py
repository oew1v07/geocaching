"""SQLite schema and connection helper shared by ingest.py, fetch_boundaries.py and stats.py."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("geocaching.db")

# Own-log types that count as a find (matches geocaching.com's find count).
FIND_LOG_TYPES = ("Found it", "Attended", "Webcam Photo Taken")

SCHEMA = """
CREATE TABLE IF NOT EXISTS imports (
    id            INTEGER PRIMARY KEY,
    source_file   TEXT NOT NULL,
    gpx_name      TEXT,
    gpx_time      TEXT,              -- <time> in the GPX header (when the PQ was generated)
    sha256        TEXT NOT NULL UNIQUE,
    imported_at   TEXT NOT NULL,
    n_caches      INTEGER,
    n_new         INTEGER,
    n_flagged     INTEGER,
    n_restored    INTEGER
);

CREATE TABLE IF NOT EXISTS caches (
    code                       TEXT PRIMARY KEY,   -- GC code
    gc_id                      TEXT,
    name                       TEXT,
    type                       TEXT,
    container                  TEXT,
    difficulty                 REAL,
    terrain                    REAL,
    lat                        REAL,
    lon                        REAL,
    country                    TEXT,
    state                      TEXT,               -- region as given by geocaching.com
    county                     TEXT,               -- from GADM boundary lookup
    county_gid                 TEXT,
    owner                      TEXT,
    placed_by                  TEXT,
    placed_date                TEXT,
    found_date                 TEXT,               -- earliest own find log date (YYYY-MM-DD)
    archived                   INTEGER,
    available                  INTEGER,
    url                        TEXT,
    first_seen_import          INTEGER REFERENCES imports(id),
    last_seen_import           INTEGER REFERENCES imports(id),
    deletion_attempted         INTEGER NOT NULL DEFAULT 0,  -- 1 = missing from the latest PQ
    deletion_attempted_import  INTEGER REFERENCES imports(id)
);

CREATE TABLE IF NOT EXISTS logs (
    log_id             TEXT PRIMARY KEY,
    code               TEXT NOT NULL REFERENCES caches(code),
    date               TEXT,     -- YYYY-MM-DD
    raw_date           TEXT,
    type               TEXT,
    finder             TEXT,
    text               TEXT,
    first_seen_import  INTEGER REFERENCES imports(id),
    last_seen_import   INTEGER REFERENCES imports(id)
);

CREATE TABLE IF NOT EXISTS cache_events (
    id         INTEGER PRIMARY KEY,
    code       TEXT NOT NULL REFERENCES caches(code),
    import_id  INTEGER NOT NULL REFERENCES imports(id),
    event      TEXT NOT NULL CHECK (event IN ('added', 'deletion_attempted', 'restored'))
);

CREATE INDEX IF NOT EXISTS idx_logs_code ON logs(code);
CREATE INDEX IF NOT EXISTS idx_events_code ON cache_events(code);
"""


def connect(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
