"""Offline demo: runs the whole flow against made-up calendars and prints every text message.

    python demo.py
"""
from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.agent import Agent
from app.config import Settings
from app.db import connect

TZ = ZoneInfo("Europe/Athens")


def ics(*events) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//demo//EN"]
    for uid, start, end, summary in events:
        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
                  f"DTEND;VALUE=DATE:{end:%Y%m%d}", f"SUMMARY:{summary}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


class PrintMessenger:
    async def send(self, rcpt, body, job_id=None):
        who = rcpt.phone or rcpt.viber_id
        print(f"   >> [{rcpt.channel:5}] to {who}: {body}")

    def log_inbound(self, *a, **k):
        pass


def show(title: str) -> None:
    print(f"\n=== {title}")


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="cleaner-demo-"))
    clock = {"now": datetime.combine(datetime.now(TZ).date(), time(10, 0), tzinfo=TZ)}
    today = clock["now"].date()

    # Two apartments: Plaka has a same-day turnover in 3 days, Kolonaki checks out in 5 days.
    out_a, out_b = today + timedelta(days=3), today + timedelta(days=5)
    (tmp / "plaka_airbnb.ics").write_text(ics(
        ("p1", out_a - timedelta(days=2), out_a, "Reserved"),
        ("p2", out_a, out_a + timedelta(days=3), "Reserved"),
        ("blk", today + timedelta(days=20), today + timedelta(days=22), "Airbnb (Not available)"),
    ))
    (tmp / "kolonaki_booking.ics").write_text(ics(("k1", out_b - timedelta(days=4), out_b, "CLOSED - Not available")))
    (tmp / "cleaners.csv").write_text(
        "name,phone,lat,lon,address,channel,language,max_jobs_per_day,viber_id\n"
        "Maria P.,+306900000001,37.9755,23.7348,,sms,el,3,\n"
        "Nikos K.,+306900000002,38.0735,23.8118,,viber,el,2,vb-nikos\n"
        "Elena D.,+306900000003,37.8650,23.7530,,sms,en,3,\n")
    (tmp / "properties.csv").write_text(
        "property_id,name,address,lat,lon,checkout_time,airbnb_ical_url,booking_ical_url\n"
        f"plaka,Plaka Studio,Adrianou 10,37.9715,23.7300,11:00,{tmp / 'plaka_airbnb.ics'},\n"
        f"kolonaki,Kolonaki Loft,Skoufa 25,37.9790,23.7440,11:00,,{tmp / 'kolonaki_booking.ics'}\n")

    s = Settings(dry_run=True, db_path=":memory:", cleaners_csv=str(tmp / "cleaners.csv"),
                 properties_csv=str(tmp / "properties.csv"), owner_phone="+306999999999")
    agent = Agent(s, connect(":memory:"), PrintMessenger(), now_fn=lambda: clock["now"])

    show("1. Load CSVs and read the Airbnb + Booking.com calendars")
    await agent.reload_data()
    await agent.sync_calendars()

    show("2. Maria (closest to everything) declines Plaka #1 -> the next closest, Elena, is asked")
    await agent.handle_inbound("sms", "+306900000001", "ΟΧΙ 1")

    show("3. Maria accepts the Kolonaki job #3")
    await agent.handle_inbound("sms", "+306900000001", "ΝΑΙ 3")

    show("4. Elena does not answer for 30 minutes -> Nikos (Viber) is asked")
    clock["now"] += timedelta(minutes=31)
    await agent.advance()

    show("5. Nikos accepts Plaka #1")
    await agent.handle_inbound("viber", "vb-nikos", "ΝΑΙ 1")

    show("6. The Kolonaki guest cancels -> job cancelled, Maria and the owner are told")
    (tmp / "kolonaki_booking.ics").write_text(ics())
    await agent.sync_calendars()

    show("7. Evening before the Plaka checkout -> reminder")
    clock["now"] = datetime.combine(out_a - timedelta(days=1), time(18, 0), tzinfo=TZ)
    await agent.send_reminders()

    show("8. Nikos finishes")
    await agent.handle_inbound("viber", "vb-nikos", "ΤΕΛΟΣ 1")

    show("Final job list")
    for j in agent.list_jobs():
        print("  ", j)


if __name__ == "__main__":
    asyncio.run(main())
