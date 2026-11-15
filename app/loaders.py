"""Load cleaners.csv and properties.csv into the database."""
from __future__ import annotations

import csv
import logging
from pathlib import Path

from .config import Settings
from .geo import geocode, normalize_phone

log = logging.getLogger(__name__)


def _rows(path: str):
    p = Path(path)
    if not p.exists():
        log.warning("%s not found", path)
        return
    with p.open(newline="", encoding="utf-8-sig") as f:
        for raw in csv.DictReader(f):
            yield {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}


def _float(v: str):
    try:
        return float(v.replace(",", ".")) if v else None
    except ValueError:
        return None


async def _coords(db, row: dict):
    lat, lon = _float(row.get("lat", "")), _float(row.get("lon", ""))
    if lat is None or lon is None:
        found = await geocode(db, row.get("address", ""))
        if found:
            lat, lon = found
    return lat, lon


async def load_cleaners(db, path: str, s: Settings) -> int:
    """Upsert cleaners by phone number (never deactivates: the web UI owns that)."""
    seen: list[str] = []
    for row in _rows(path) or []:
        phone = normalize_phone(row.get("phone", ""), s.default_country_code)
        if not phone or not row.get("name"):
            log.warning("skipping cleaner row without name/phone: %s", row)
            continue
        lat, lon = await _coords(db, row)
        if lat is None:
            log.warning("cleaner %s has no usable location (give lat/lon or a geocodable address); skipped", row["name"])
            continue
        channel = "telegram"  # the only channel for now (SMS / Viber code is kept but not offered)
        lang = row.get("language", "en").lower()
        lang = lang if lang in ("en", "el") else "en"
        max_jobs = int(row["max_jobs_per_day"]) if row.get("max_jobs_per_day", "").isdigit() else s.default_max_jobs_per_day
        db.execute(
            """INSERT INTO cleaners(name, phone, lat, lon, address, channel, language, viber_id, active, max_jobs_per_day, email)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULLIF(?, ''), 1, ?, NULLIF(?, ''))
               ON CONFLICT(phone) DO UPDATE SET
                 name=excluded.name, lat=excluded.lat, lon=excluded.lon, address=excluded.address,
                 channel=excluded.channel, language=excluded.language,
                 max_jobs_per_day=excluded.max_jobs_per_day,
                 email=COALESCE(excluded.email, cleaners.email),
                 viber_id=COALESCE(NULLIF(excluded.viber_id, ''), cleaners.viber_id)""",
            (row["name"], phone, lat, lon, row.get("address", ""), channel, lang, row.get("viber_id", ""), max_jobs, row.get("email", "")),
        )
        seen.append(phone)
    log.info("loaded %d cleaners", len(seen))
    return len(seen)


async def load_properties(db, path: str, s: Settings) -> int:
    """Upsert properties and replace their calendar feeds."""
    seen: list[str] = []
    for row in _rows(path) or []:
        pid = row.get("property_id", "")
        if not pid or not row.get("name"):
            log.warning("skipping property row without id/name: %s", row)
            continue
        lat, lon = await _coords(db, row)
        if lat is None:
            log.warning("property %s has no usable location; skipped", pid)
            continue
        db.execute(
            """INSERT INTO properties(id, name, address, lat, lon, checkout_time) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, address=excluded.address,
                 lat=excluded.lat, lon=excluded.lon, checkout_time=excluded.checkout_time""",
            (pid, row["name"], row.get("address", ""), lat, lon, row.get("checkout_time") or s.default_checkout_time),
        )
        db.execute("DELETE FROM calendars WHERE property_id=?", (pid,))
        for source, col in (("airbnb", "airbnb_ical_url"), ("booking", "booking_ical_url"), ("other", "other_ical_url")):
            if row.get(col):
                db.execute("INSERT INTO calendars(property_id, source, url) VALUES (?, ?, ?)", (pid, source, row[col]))
        seen.append(pid)
    log.info("loaded %d properties", len(seen))
    return len(seen)
