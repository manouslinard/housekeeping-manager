from __future__ import annotations

import tempfile
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.agent import Agent
from app.config import Settings
from app.db import connect

TZ = ZoneInfo("Europe/Athens")
TODAY = date(2026, 11, 14)


def ics(*events: tuple[str, date, date, str]) -> str:
    """events: (uid, start, end, summary)"""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN"]
    for uid, start, end, summary in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
            f"DTEND;VALUE=DATE:{end:%Y%m%d}",
            f"SUMMARY:{summary}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


class FakeMessenger:
    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []  # (channel, party, body)
        self.fail_for: set[str] = set()

    async def send(self, rcpt, body, job_id=None):
        from app.messaging import SendError

        if rcpt.phone in self.fail_for:
            raise SendError("boom")
        channel = rcpt.channel
        if (channel == "viber" and not rcpt.viber_id) or (channel == "telegram" and not rcpt.telegram_id):
            channel = "sms"  # same fallback as the real Messenger
        party = {"viber": rcpt.viber_id, "telegram": rcpt.telegram_id}.get(channel) or rcpt.phone
        self.sent.append((channel, party, body))

    def log_inbound(self, *a, **k):
        pass

    def to(self, phone: str) -> list[str]:
        return [b for _, p, b in self.sent if p == phone]


class Env:
    """A fully wired agent with a controllable clock and calendar feeds."""

    def __init__(self, llm=None, **overrides):
        xlsx = tempfile.mkdtemp() + "/schedules.xlsx"
        self.settings = Settings(**{"dry_run": True, "owner_phone": "+306999999999", "skip_sunday": False,
                                    "schedule_xlsx": xlsx, **overrides})
        self.db = connect(":memory:")
        self.messenger = FakeMessenger()
        self.feeds: dict[str, str] = {}
        self.broken: set[str] = set()
        self.clock = datetime.combine(TODAY, time(10, 0), tzinfo=TZ)
        self.agent = Agent(self.settings, self.db, self.messenger, now_fn=lambda: self.clock, fetch_fn=self._fetch, llm=llm)

    async def _fetch(self, url: str) -> str:
        if url in self.broken:
            raise RuntimeError("network down")
        return self.feeds[url]

    def add_cleaner(self, name, phone, lat, lon, channel="sms", lang="en", max_jobs=3, viber_id=None, telegram_id=None):
        self.db.execute(
            "INSERT INTO cleaners(name, phone, lat, lon, channel, language, viber_id, telegram_id, max_jobs_per_day) VALUES (?,?,?,?,?,?,?,?,?)",
            (name, phone, lat, lon, channel, lang, viber_id, telegram_id, max_jobs),
        )

    def add_property(self, pid, name, lat, lon, urls: dict[str, str]):
        self.db.execute("INSERT INTO properties(id, name, address, lat, lon) VALUES (?,?,?,?,?)", (pid, name, f"{name} St", lat, lon))
        for source, url in urls.items():
            self.db.execute("INSERT INTO calendars(property_id, source, url) VALUES (?,?,?)", (pid, source, url))

    def advance_minutes(self, m: int):
        self.clock += timedelta(minutes=m)

    def set_time(self, hour: int):
        self.clock = datetime.combine(self.clock.date(), time(hour, 0), tzinfo=TZ)


@pytest.fixture
def env():
    e = Env()
    # Property in Plaka. Maria is ~1 km away, Nikos ~10 km, Elena ~12 km.
    e.add_property("plaka", "Plaka Studio", 37.9715, 23.7300, {"airbnb": "airbnb://plaka"})
    e.add_cleaner("Maria", "+306900000001", 37.9755, 23.7348)
    e.add_cleaner("Nikos", "+306900000002", 38.0735, 23.8118, channel="viber", viber_id="vb-nikos")
    e.add_cleaner("Elena", "+306900000003", 37.8650, 23.7530)
    return e
