"""Read Airbnb / Booking.com iCal feeds and turn them into reservations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
from icalendar import Calendar


@dataclass(frozen=True)
class Reservation:
    uid: str
    start: date          # check-in date
    end: date            # check-out date  -> a cleaning is needed on this day
    summary: str
    source: str          # airbnb | booking | other | manual
    checkout_time: str | None = None  # known for manual/Excel bookings


def _to_date(v) -> date:
    return v.date() if isinstance(v, datetime) else v


def parse_ics(text: str, source: str, ignore_blocks: bool = True) -> list[Reservation]:
    """Parse one iCal feed.

    Airbnb exports guest stays as "Reserved" and owner blocks as "Airbnb (Not available)",
    so blocks are skipped for the airbnb source. Booking.com labels reservations
    "CLOSED - Not available", so for other sources every event counts as a stay.
    """
    cal = Calendar.from_ical(text)
    out: list[Reservation] = []
    for ev in cal.walk("VEVENT"):
        summary = str(ev.get("SUMMARY", "") or "")
        status = str(ev.get("STATUS", "") or "").upper()
        if status == "CANCELLED":
            continue
        if source == "airbnb" and ignore_blocks and "not available" in summary.lower():
            continue
        dtstart = ev.get("DTSTART")
        if dtstart is None:
            continue
        start = _to_date(dtstart.dt)
        dtend = ev.get("DTEND")
        end = _to_date(dtend.dt) if dtend is not None else start + timedelta(days=1)
        if end <= start:
            continue
        uid = str(ev.get("UID", "") or f"{start}-{end}")
        out.append(Reservation(uid=uid, start=start, end=end, summary=summary, source=source))
    return out


async def fetch_ics(url: str) -> str:
    """Download a feed. Plain file paths are accepted too (handy for demos and tests)."""
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if url.startswith(("http://", "https://")):
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.text
    return Path(url.removeprefix("file://")).read_text(encoding="utf-8")
