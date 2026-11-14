from datetime import date, datetime, time
from pathlib import Path

import app.api as api
from app.agent import Agent  # noqa: F401

from .conftest import TZ, Env
from .test_api import make_client

XLSX = Path(__file__).parent.parent / "Kratiseis_simple_demo.xlsx"


def client(monkeypatch, today=date(2026, 6, 29)):
    async def fake_geocode(db, address):
        return (37.97, 23.73)

    monkeypatch.setattr(api, "geocode", fake_geocode)
    env = Env(skip_sunday=True)
    env.clock = datetime.combine(today, time(10, 0), tzinfo=TZ)
    return env, make_client(env)


def test_excel_import_builds_rooms_bookings_and_jobs(monkeypatch):
    env, c = client(monkeypatch, today=date(2026, 11, 14))
    with c:
        assert c.post("/api/rooms", json={"hotel": "Housekeeping", "address": "Athens", "rooms": "1-6"}).json()["ok"]
        r = c.post("/api/import", data={"hotel": "Housekeeping"}, files={"file": ("k.xlsx", XLSX.read_bytes())})
        assert r.status_code == 200, r.text
        s = c.get("/api/state").json()
        assert len(s["rooms"]) == 6 and len(s["bookings"]) == 12
        assert s["jobs"], "imported stays must produce cleaning jobs"
        # Sunday rule: no job is ever scheduled on a Sunday unless a guest arrives that day
        starts = {(b["property_id"], b["checkin_date"]) for b in s["bookings"]}
        for j in s["jobs"]:
            d = date.fromisoformat(j["checkout_date"])
            assert d.weekday() != 6 or (j["property_id"], j["checkout_date"]) in starts
        # re-import adds nothing: every row overlaps itself
        again = c.post("/api/import", data={"hotel": "Housekeeping"}, files={"file": ("k.xlsx", XLSX.read_bytes())}).json()
        assert again["bookings"] == 0 and again["skipped"] == 12
        assert len(c.get("/api/state").json()["bookings"]) == len(s["bookings"])


def test_manual_booking_and_cleaner(monkeypatch):
    env, c = client(monkeypatch)
    with c:
        c.post("/api/rooms", json={"hotel": "Housekeeping", "address": "Athens", "rooms": "5"})
        assert c.post("/api/cleaners", json={"name": "Maria", "phone": "690 000 0001", "address": "Plaka"}).json()["ok"]
        pid = c.get("/api/state").json()["rooms"][0]["id"]
        b = {"property_id": pid, "checkin_date": "2026-07-06", "checkout_date": "2026-07-08", "checkout_time": "12:30"}
        assert c.post("/api/bookings", json=b).status_code == 200
        job = c.get("/api/state").json()["jobs"][0]
        assert (job["checkout_date"], job["checkout_time"]) == ("2026-07-08", "12:30")
        assert job["status"] == "offering" and job["offered_to"] == "Maria" and job["offered_km"] >= 0  # UI shows who was asked
        assert c.post("/api/bookings", json={**b, "checkout_date": "2026-07-06"}).status_code == 422
        assert c.post("/api/bookings", json={**b, "checkin_date": "2026-07-07"}).status_code == 422  # overlaps
        assert c.post("/api/bookings", json={**b, "checkin_date": "2026-07-08", "checkout_date": "2026-07-09"}).status_code == 200  # same-day turnover
        for bk in c.get("/api/state").json()["bookings"]:
            c.delete(f"/api/bookings/{bk['id']}")
        assert c.get("/api/state").json()["jobs"] == []


def test_simple_excel_skips_overlaps(monkeypatch):
    env, c = client(monkeypatch)
    simple = Path(__file__).parent.parent / "Kratiseis_simple_demo.xlsx"
    with c:
        c.post("/api/rooms", json={"hotel": "Aegean", "address": "Athens", "rooms": "1-6"})
        first = c.post("/api/import", data={"hotel": "Aegean"}, files={"file": ("s.xlsx", simple.read_bytes())}).json()
        assert (first["bookings"], first["skipped"]) == (12, 0)
        again = c.post("/api/import", data={"hotel": "Aegean"}, files={"file": ("s.xlsx", simple.read_bytes())}).json()
        assert (again["bookings"], again["skipped"]) == (0, 12)
