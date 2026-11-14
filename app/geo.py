"""Distance, phone normalisation and (optional) address geocoding."""
from __future__ import annotations

import asyncio
import logging
import math
import re

import httpx

log = logging.getLogger(__name__)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def normalize_phone(raw: str, default_cc: str = "+30") -> str:
    """Return an E.164-style number, or '' if nothing usable was given."""
    s = re.sub(r"[^\d+]", "", raw or "")
    if not s:
        return ""
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s.startswith("+"):
        s = default_cc + s.lstrip("0")
    return s


async def geocode(db, address: str) -> tuple[float, float] | None:
    """Look an address up on OpenStreetMap Nominatim (results are cached in the DB)."""
    address = (address or "").strip()
    if not address:
        return None
    row = db.execute("SELECT lat, lon FROM geocache WHERE address=?", (address,)).fetchone()
    if row is not None:
        return (row["lat"], row["lon"]) if row["lat"] is not None else None
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "cleaner-agent/1.0"}) as c:
            r = await c.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": address, "format": "json", "limit": 1},
            )
            r.raise_for_status()
            data = r.json()
        await asyncio.sleep(1.1)  # Nominatim usage policy: max 1 request/second
    except Exception as exc:  # network down, rate limited...
        log.warning("geocoding %r failed: %s", address, exc)
        return None
    if not data:
        db.execute("INSERT OR REPLACE INTO geocache VALUES (?, NULL, NULL)", (address,))
        return None
    lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
    db.execute("INSERT OR REPLACE INTO geocache VALUES (?, ?, ?)", (address, lat, lon))
    return lat, lon
