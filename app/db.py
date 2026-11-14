"""SQLite schema and connection helper."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cleaners (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    lat REAL, lon REAL, address TEXT,
    channel TEXT NOT NULL DEFAULT 'sms',        -- sms | viber
    language TEXT NOT NULL DEFAULT 'en',        -- en | el
    viber_id TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    max_jobs_per_day INTEGER NOT NULL DEFAULT 3
);

CREATE TABLE IF NOT EXISTS properties (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT,
    lat REAL, lon REAL,
    checkout_time TEXT NOT NULL DEFAULT '11:00',
    hotel TEXT, room TEXT                        -- set for rooms added from the UI
);

CREATE TABLE IF NOT EXISTS bookings (            -- manual / Excel-imported stays (iCal feeds live in `calendars`)
    id INTEGER PRIMARY KEY,
    property_id TEXT NOT NULL REFERENCES properties(id),
    code TEXT NOT NULL,
    checkin_date TEXT NOT NULL, checkin_time TEXT NOT NULL DEFAULT '15:00',
    checkout_date TEXT NOT NULL, checkout_time TEXT NOT NULL DEFAULT '11:00',
    guests INTEGER, guest_name TEXT, source TEXT, notes TEXT,
    UNIQUE(property_id, code)
);

CREATE TABLE IF NOT EXISTS calendars (
    id INTEGER PRIMARY KEY,
    property_id TEXT NOT NULL REFERENCES properties(id),
    source TEXT NOT NULL,                        -- airbnb | booking | other
    url TEXT NOT NULL,
    UNIQUE(property_id, url)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    property_id TEXT NOT NULL REFERENCES properties(id),
    checkout_date TEXT NOT NULL,                 -- YYYY-MM-DD
    checkout_time TEXT NOT NULL,
    next_checkin_date TEXT,                      -- equal to checkout_date => same-day turnover
    kind TEXT NOT NULL DEFAULT 'checkout',       -- checkout | stay (mid-stay cleaning, guest still in)
    status TEXT NOT NULL DEFAULT 'pending',      -- pending|offering|assigned|completed|cancelled|unfilled
    cleaner_id INTEGER REFERENCES cleaners(id),
    reminded_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_jobs_active
    ON jobs(property_id, checkout_date) WHERE status != 'cancelled';

CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    cleaner_id INTEGER NOT NULL REFERENCES cleaners(id),
    status TEXT NOT NULL DEFAULT 'pending',      -- pending|accepted|declined|expired|cancelled|failed|retired
    distance_km REAL,
    sent_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    responded_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    direction TEXT NOT NULL,                     -- out | in
    channel TEXT NOT NULL,
    party TEXT NOT NULL,                         -- phone number or viber id
    body TEXT NOT NULL,
    job_id INTEGER,
    provider TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS geocache (
    address TEXT PRIMARY KEY,
    lat REAL, lon REAL
);
"""


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(properties)")}
    for col in ("hotel", "room"):  # upgrade databases created before the UI existed
        if col not in have:
            conn.execute(f"ALTER TABLE properties ADD COLUMN {col} TEXT")
    if "kind" not in {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}:
        conn.execute("ALTER TABLE jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'checkout'")
    return conn
