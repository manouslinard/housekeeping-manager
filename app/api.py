"""JSON API + Excel import used by the web UI (static/index.html)."""
from __future__ import annotations

import io
import re
from datetime import date, datetime, time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .geo import geocode, normalize_phone


class CleanerIn(BaseModel):
    name: str
    phone: str
    address: str
    channel: str = "sms"          # sms | viber | telegram


class RoomIn(BaseModel):
    hotel: str
    address: str
    rooms: str            # "5", "1,2,7" or "1-16"
    checkout_time: str = "11:00"


class BookingIn(BaseModel):
    property_id: str
    checkin_date: date
    checkin_time: str = "15:00"
    checkout_date: date
    checkout_time: str = "11:00"
    guests: int | None = None
    guest_name: str = ""


def expand_rooms(spec: str) -> list[str]:
    out: list[str] = []
    for part in re.split(r"[,\s;]+", spec.strip()):
        m = re.fullmatch(r"(\d+)-(\d+)", part)
        if m and int(m[1]) <= int(m[2]) <= int(m[1]) + 500:
            out += [str(i) for i in range(int(m[1]), int(m[2]) + 1)]
        elif part:
            out.append(part)
    return list(dict.fromkeys(out))


def slug(s: str) -> str:
    return re.sub(r"[^\w]+", "-", s.strip().lower()).strip("-")


def hhmm(v, default: str) -> str:
    if isinstance(v, (time, datetime)):
        return f"{v.hour:02d}:{v.minute:02d}"
    return v if isinstance(v, str) and re.fullmatch(r"\d{1,2}:\d{2}", v.strip()) else default


def as_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    return v if isinstance(v, date) else None


# Excel header -> field. Matched by keyword so the column order doesn't matter.
def _field(h: str) -> str | None:
    h = h.lower()
    timey = h.startswith("ώρα") or "time" in h
    if "κωδικ" in h or "code" in h or "reservation" in h:
        return "code"
    if "δωμάτιο" in h or "room" in h:
        return "room"
    if "check-in" in h or "arrival" in h:
        return "in_time" if timey else "in_date"
    if "check-out" in h or "departure" in h:
        return "out_time" if timey else "out_date"
    if "επισκέπτες" in h or "guests" in h:
        return "guests"
    if "όνομα" in h or "name" in h:
        return "guest_name"
    if "πηγή" in h or "source" in h:
        return "source"
    if "σημειώσεις" in h or "notes" in h:
        return "notes"
    return None


def parse_workbook(data: bytes) -> list[dict]:
    """Rows of the first sheet that has check-in/check-out/room columns. Maintenance rows are skipped."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    for ws in wb.worksheets:
        rows = ws.iter_rows(values_only=True)
        cols = {}
        for i, h in enumerate(next(rows, ()) or ()):
            f = _field(str(h or ""))
            if f and f not in cols.values():
                cols[i] = f
        if {"room", "in_date", "out_date"} <= set(cols.values()):
            break
    else:
        raise HTTPException(422, "Δεν βρέθηκε φύλλο με στήλες Δωμάτιο / Check-in / Check-out")
    out = []
    for r in rows:
        d = {f: r[i] for i, f in cols.items() if i < len(r)}
        if d.get("source") == "Συντήρηση" or str(d.get("code") or "").startswith("MNT"):
            continue  # maintenance block, not a guest
        cin, cout = as_date(d.get("in_date")), as_date(d.get("out_date"))
        if d.get("room") in (None, "") or not cin or not cout or cout <= cin:
            continue
        room = str(int(d["room"])) if isinstance(d["room"], float) and d["room"].is_integer() else str(d["room"]).strip()
        out.append({
            "room": room, "code": str(d.get("code") or f"{room}-{cin:%Y%m%d}"),
            "checkin_date": cin.isoformat(), "checkin_time": hhmm(d.get("in_time"), "15:00"),
            "checkout_date": cout.isoformat(), "checkout_time": hhmm(d.get("out_time"), "11:00"),
            "guests": d.get("guests") if isinstance(d.get("guests"), int) else None,
            "guest_name": str(d.get("guest_name") or ""), "source": str(d.get("source") or "Excel"),
            "notes": str(d.get("notes") or ""),
        })
    return out


def taken(a, pid: str, cin: str, cout: str) -> bool:
    """Is the room already booked for any night in [cin, cout)? Same-day turnover (out == next in) is fine."""
    return a.q1("SELECT 1 FROM bookings WHERE property_id=? AND checkin_date < ? AND checkout_date > ?", (pid, cout, cin)) is not None


def make_router(admin) -> APIRouter:
    r = APIRouter(prefix="/api", dependencies=[Depends(admin)])

    def ag(request: Request):
        return request.app.state.agent

    @r.get("/state")
    async def state(request: Request):
        a = ag(request)
        rows = lambda sql: [dict(x) for x in a.q(sql)]  # noqa: E731
        return {
            "rooms": rows("SELECT id, name, address, hotel, room, checkout_time FROM properties ORDER BY COALESCE(hotel, name), CAST(room AS INTEGER), room"),
            "cleaners": rows("SELECT id, name, phone, address, channel, max_jobs_per_day, telegram_id IS NOT NULL AS linked FROM cleaners WHERE active=1 ORDER BY name"),
            "bookings": rows("SELECT * FROM bookings ORDER BY checkin_date"),
            "jobs": [j for j in a.list_jobs() if j["status"] != "cancelled"],
            "today": a.now().date().isoformat(),
        }

    @r.post("/cleaners")
    async def add_cleaner(body: CleanerIn, request: Request):
        a = ag(request)
        phone = normalize_phone(body.phone, a.s.default_country_code)
        if not body.name.strip() or not phone:
            raise HTTPException(422, "Χρειάζονται όνομα και τηλέφωνο")
        pos = await geocode(a.db, body.address)
        if not pos:
            raise HTTPException(422, "Η διεύθυνση δεν βρέθηκε. Δοκίμασε πιο πλήρη διεύθυνση (οδός, αριθμός, πόλη).")
        a.x(
            """INSERT INTO cleaners(name, phone, lat, lon, address, channel, active, max_jobs_per_day) VALUES (?,?,?,?,?,?,1,?)
               ON CONFLICT(phone) DO UPDATE SET name=excluded.name, lat=excluded.lat, lon=excluded.lon,
                 address=excluded.address, channel=excluded.channel, active=1""",
            (body.name.strip(), phone, *pos, body.address, body.channel if body.channel in ("sms", "viber", "telegram") else "sms",
             a.s.default_max_jobs_per_day),
        )
        return {"ok": True}

    @r.get("/cleaners/{cid}/telegram-link")
    async def telegram_link(cid: int, request: Request):
        """One-tap link for a cleaner: t.me/<bot>?start=<single-use code>. She taps it, presses Start, done."""
        a = ag(request)
        if not a.s.telegram_bot_token:
            raise HTTPException(422, "Δεν έχει οριστεί TELEGRAM_BOT_TOKEN στο .env")
        if not a.s.telegram_bot_username:
            from .telegram import bot_username

            a.s.telegram_bot_username = await bot_username(a.s.telegram_bot_token)
            if not a.s.telegram_bot_username:
                raise HTTPException(502, "Δεν βρέθηκε το όνομα του bot (getMe). Όρισε TELEGRAM_BOT_USERNAME στο .env")
        code = a.telegram_link_token(cid)
        if code is None:
            raise HTTPException(404, "Άγνωστη καθαρίστρια")
        c = a.q1("SELECT name, telegram_id FROM cleaners WHERE id=?", (cid,))
        return {"url": f"https://t.me/{a.s.telegram_bot_username}?start={code}", "name": c["name"],
                "linked": c["telegram_id"] is not None}

    @r.delete("/cleaners/{cid}")
    async def del_cleaner(cid: int, request: Request):
        ag(request).x("UPDATE cleaners SET active=0 WHERE id=?", (cid,))  # soft: jobs/offers keep their history
        return {"ok": True}

    async def ensure_rooms(a, hotel: str, address: str, numbers: list[str], checkout_time: str) -> list[str]:
        pos = await geocode(a.db, address)
        if not pos:
            raise HTTPException(422, "Η διεύθυνση δεν βρέθηκε. Δοκίμασε πλήρη μορφή: οδός, αριθμός, περιοχή, πόλη (π.χ. «Αδριανού 10, Πλάκα, Αθήνα»).")
        ids = []
        for n in numbers:
            pid = f"{slug(hotel)}-{slug(n)}"
            a.x(
                """INSERT INTO properties(id, name, address, lat, lon, checkout_time, hotel, room) VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET address=excluded.address, lat=excluded.lat, lon=excluded.lon""",
                (pid, f"{hotel} – {n}", address, *pos, checkout_time, hotel, n),
            )
            ids.append(pid)
        return ids

    @r.post("/rooms")
    async def add_rooms(body: RoomIn, request: Request):
        numbers = expand_rooms(body.rooms)
        if not body.hotel.strip() or not numbers:
            raise HTTPException(422, "Χρειάζονται ξενοδοχείο και αριθμός δωματίου")
        ids = await ensure_rooms(ag(request), body.hotel.strip(), body.address, numbers, hhmm(body.checkout_time, "11:00"))
        return {"ok": True, "rooms": ids}

    async def resync(a):
        await a.sync_calendars()

    @r.delete("/hotels")
    async def del_hotel(name: str, request: Request):
        """Remove a hotel with all its rooms, bookings and cleaning jobs (the CSV loader may re-add CSV rooms on restart)."""
        a = ag(request)
        ids = [x["id"] for x in a.q("SELECT id FROM properties WHERE COALESCE(hotel, name)=?", (name,))]
        if not ids:
            raise HTTPException(404, "Άγνωστο ξενοδοχείο")
        marks = ",".join("?" * len(ids))
        a.x(f"DELETE FROM offers WHERE job_id IN (SELECT id FROM jobs WHERE property_id IN ({marks}))", ids)
        for table in ("jobs", "bookings", "calendars"):
            a.x(f"DELETE FROM {table} WHERE property_id IN ({marks})", ids)
        a.x(f"DELETE FROM properties WHERE id IN ({marks})", ids)
        return {"ok": True, "rooms": len(ids)}

    @r.post("/bookings")
    async def add_booking(body: BookingIn, request: Request):
        a = ag(request)
        if body.checkout_date <= body.checkin_date:
            raise HTTPException(422, "Το check-out πρέπει να είναι μετά το check-in")
        if not a.q1("SELECT 1 FROM properties WHERE id=?", (body.property_id,)):
            raise HTTPException(404, "Άγνωστο δωμάτιο")
        if taken(a, body.property_id, body.checkin_date.isoformat(), body.checkout_date.isoformat()):
            raise HTTPException(422, "Το δωμάτιο έχει ήδη κράτηση για αυτές τις ημέρες")
        a.x(
            """INSERT INTO bookings(property_id, code, checkin_date, checkin_time, checkout_date, checkout_time, guests, guest_name, source)
               VALUES (?,?,?,?,?,?,?,?, 'Χειροκίνητη')""",
            (body.property_id, f"M{datetime.now():%y%m%d%H%M%S%f}", body.checkin_date.isoformat(), hhmm(body.checkin_time, "15:00"),
             body.checkout_date.isoformat(), hhmm(body.checkout_time, "11:00"), body.guests, body.guest_name.strip()),
        )
        await resync(a)
        return {"ok": True}

    @r.delete("/bookings/{bid}")
    async def del_booking(bid: int, request: Request):
        a = ag(request)
        a.x("DELETE FROM bookings WHERE id=?", (bid,))
        await resync(a)
        return {"ok": True}

    @r.post("/import")
    async def import_excel(request: Request, file: UploadFile = File(...), hotel: str = Form(...)):
        """Import stays for an existing hotel. Rooms mentioned in the file but not yet created are added."""
        a = ag(request)
        first = a.q1("SELECT address, checkout_time FROM properties WHERE hotel=? LIMIT 1", (hotel,))
        if not first:
            raise HTTPException(422, "Πρόσθεσε πρώτα το ξενοδοχείο (κουμπί «Ξενοδοχείο»)")
        try:
            rows = parse_workbook(await file.read())
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(422, "Μη έγκυρο αρχείο Excel")
        if not rows:
            raise HTTPException(422, "Δεν βρέθηκαν κρατήσεις στο αρχείο")
        ids = await ensure_rooms(a, hotel, first["address"], sorted({x["room"] for x in rows}, key=lambda s: (len(s), s)), first["checkout_time"])
        added = []
        for x in rows:
            pid = f"{slug(hotel)}-{slug(x['room'])}"
            if taken(a, pid, x["checkin_date"], x["checkout_date"]):
                continue  # same room already booked those days (an identical row counts too, so re-importing is harmless)
            added.append(x)
            a.x(
                """INSERT OR IGNORE INTO bookings(property_id, code, checkin_date, checkin_time, checkout_date, checkout_time, guests, guest_name, source, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (pid, x["code"], x["checkin_date"], x["checkin_time"], x["checkout_date"],
                 x["checkout_time"], x["guests"], x["guest_name"], x["source"], x["notes"]),
            )
        await resync(a)
        return {"ok": True, "bookings": len(added), "skipped": len(rows) - len(added), "rooms": len(ids),
                "first_date": min((x["checkin_date"] for x in added), default=None)}

    return r
