"""Reset the database to a clean slate, optionally filled with demo data.

    python reset_demo.py          # wipe everything, then load demo cleaners, a hotel, rooms and bookings
    python reset_demo.py --empty  # wipe everything, load nothing

Safe to run while the server is up. Refuses to run when real messaging is configured (Twilio/Viber),
because the demo cleaners have fake phone numbers; use --force to override.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import urllib.request
from datetime import date, timedelta

from app.config import Settings
from app.db import connect
from app.loaders import load_cleaners

ROOMS = 3          # small demo: easy to follow the offers one by one
DAYS = 10          # bookings are generated for the next 10 days
HOTEL, ADDRESS, LAT, LON = "Hotel Aegean", "Adrianou 10, Plaka, Athens", 37.9715, 23.7300
GUESTS = ["Daniel Ashworth", "Γιώργος Λαμπράκης", "Sophie Bennett", "Ελένη Μελισσάνου", "Julien Laurent", "Camille Chevalier",
          "Νικόλαος Καραγιάννης", "Hannah Whitfield", "Mathieu Cheval", "Δέσποινα Δρακάκη", "Thomas Collins", "Elena Marchetti",
          "Παναγιώτης Σταμάτης", "Charlotte Marsh", "Maximilian Hoff", "Σοφία Λαμπράκη", "Luca Fontana", "Anastasia Dimou"]


def seed_bookings(db, today: date, rooms: int = ROOMS) -> int:
    rnd, n = random.Random(42), 0  # fixed seed: the same demo every time
    for room in range(1, rooms + 1):
        pid, day = f"{HOTEL.lower().replace(' ', '-')}-{room}", today - timedelta(days=rnd.randint(0, 2))
        while day < today + timedelta(days=DAYS):
            nights = rnd.randint(2, 7)
            out = day + timedelta(days=nights)
            db.execute(
                """INSERT INTO bookings(property_id, code, checkin_date, checkin_time, checkout_date, checkout_time, guests, guest_name, source)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (pid, f"DEMO-{room}-{day:%m%d}", day.isoformat(), "15:00", out.isoformat(), rnd.choice(["10:00", "11:00", "12:00"]),
                 rnd.randint(1, 3), rnd.choice(GUESTS), rnd.choice(["Booking", "Airbnb", "Direct"])),
            )
            n += 1
            day = out + timedelta(days=rnd.choice([0, 2, 3, 4]))  # 0 = same-day turnover
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--empty", action="store_true", help="wipe only, no demo data")
    ap.add_argument("--force", action="store_true", help="run even when Twilio/Viber are configured")
    args = ap.parse_args()
    s = Settings.from_env()
    if (s.twilio_auth_token or s.viber_auth_token) and not args.force:
        raise SystemExit("Real messaging is configured (DRY_RUN is off). Refusing; pass --force if you really mean it.")

    db = connect(s.db_path)
    links = db.execute("SELECT phone, viber_id, telegram_id FROM cleaners WHERE viber_id IS NOT NULL OR telegram_id IS NOT NULL").fetchall()
    for table in ("unavailability", "offers", "jobs", "bookings", "calendars", "properties", "cleaners", "messages"):
        db.execute(f"DELETE FROM {table}")
    print("wiped", s.db_path)
    if args.empty:
        return

    asyncio.run(load_cleaners(db, s.cleaners_csv, s))  # demo cleaners live in data/cleaners.csv
    for r in links:  # keep chats that were already linked to the bot
        db.execute("UPDATE cleaners SET viber_id=COALESCE(?, viber_id), telegram_id=COALESCE(?, telegram_id) WHERE phone=?",
                   (r["viber_id"], r["telegram_id"], r["phone"]))
    for room in range(1, ROOMS + 1):
        db.execute(
            "INSERT INTO properties(id, name, address, lat, lon, checkout_time, hotel, room) VALUES (?,?,?,?,?,?,?,?)",
            (f"{HOTEL.lower().replace(' ', '-')}-{room}", f"{HOTEL} – {room}", ADDRESS, LAT, LON, "11:00", HOTEL, str(room)),
        )
    n = seed_bookings(db, date.today())
    ncl = db.execute("SELECT COUNT(*) FROM cleaners WHERE active=1").fetchone()[0]
    print(f"loaded {ncl} cleaners, {ROOMS} rooms, {n} bookings")

    # ask a running server to turn the bookings into cleaning jobs now (otherwise it happens within SYNC_INTERVAL_MIN)
    port = os.environ.get("PORT", "8000")
    try:
        req = urllib.request.Request(f"http://localhost:{port}/admin/sync", method="POST", headers={"X-Admin-Token": s.admin_token})
        urllib.request.urlopen(req, timeout=20)
        print("server synced: cleaning jobs created")
    except Exception:
        print("server not reachable: jobs appear on its next sync (every", s.sync_interval_min, "min or at startup)")


if __name__ == "__main__":
    main()
