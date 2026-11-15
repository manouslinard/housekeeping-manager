"""Per-cleaner programme as an Excel file, derived from the database.

The DB stays the source of truth. After every change the agent rewrites this workbook, and Gemini reads it
back (`read_schedule`) to see who is free, who already works near a room, who is away, and so on.

Sheets: `Cleaners` (one row each), `Schedule` (one row per job a cleaner holds or has been offered),
`Open jobs` (jobs nobody holds yet), and one sheet per cleaner with her own programme for easy reading.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import openpyxl

CLEANER_COLS = ["cleaner_id", "name", "phone", "address", "channel", "language", "max_jobs_per_day", "unavailable"]
SCHEDULE_COLS = ["cleaner_id", "cleaner", "date", "weekday", "start", "end", "job_id", "hotel", "room", "kind",
                 "status", "km"]
OPEN_COLS = ["job_id", "date", "weekday", "start", "hotel", "room", "kind", "status"]


def _end(start: str, minutes: int) -> str:
    h, _, m = start.partition(":")
    total = int(h) * 60 + int(m or 0) + minutes
    return f"{total // 60 % 24:02d}:{total % 60:02d}"


def build_rows(agent, days: int = 21) -> dict[str, list[dict]]:
    """Plain dict rows for the three tables (also what the tests look at)."""
    today = agent.now().date()
    last = today + timedelta(days=days)
    cleaning = agent.s.cleaning_min

    unavail: dict[int, list[str]] = {}
    for u in agent.q("SELECT * FROM unavailability WHERE to_date >= ? ORDER BY from_date", (today.isoformat(),)):
        span = u["from_date"] if u["from_date"] == u["to_date"] else f'{u["from_date"]}..{u["to_date"]}'
        unavail.setdefault(u["cleaner_id"], []).append(span)

    cleaners = [
        {"cleaner_id": c["id"], "name": c["name"], "phone": c["phone"], "address": c["address"] or "",
         "channel": c["channel"], "language": c["language"], "max_jobs_per_day": c["max_jobs_per_day"],
         "unavailable": "; ".join(unavail.get(c["id"], []))}
        for c in agent.q("SELECT * FROM cleaners WHERE active=1 ORDER BY name")
    ]

    def job_row(j, status: str) -> dict:
        d = date.fromisoformat(j["checkout_date"])
        return {"job_id": j["id"], "date": j["checkout_date"], "weekday": d.strftime("%a"), "start": j["checkout_time"],
                "hotel": j["hotel"] or j["pname"], "room": j["room"] or "", "kind": j["kind"], "status": status}

    sql = """SELECT j.*, p.name AS pname, p.hotel, p.room, COALESCE(j.cleaner_id, o.cleaner_id) AS cid,
                    o.distance_km AS km, c.name AS cname
             FROM jobs j JOIN properties p ON p.id=j.property_id
             LEFT JOIN offers o ON o.job_id=j.id AND o.status='pending'
             LEFT JOIN cleaners c ON c.id=COALESCE(j.cleaner_id, o.cleaner_id)
             WHERE j.checkout_date BETWEEN ? AND ? AND j.status IN ('pending','offering','assigned','unfilled')
             ORDER BY j.checkout_date, j.checkout_time, j.id"""
    schedule, open_jobs = [], []
    for j in agent.q(sql, (today.isoformat(), last.isoformat())):
        if j["cid"] is None:
            open_jobs.append(job_row(j, j["status"]))
            continue
        r = job_row(j, "assigned" if j["status"] == "assigned" else "offered")
        r.update(cleaner_id=j["cid"], cleaner=j["cname"], end=_end(j["checkout_time"], cleaning),
                 km=round(j["km"], 1) if j["km"] is not None else "")
        schedule.append(r)
    schedule.sort(key=lambda r: (r["cleaner"], r["date"], r["start"]))
    return {"cleaners": cleaners, "schedule": schedule, "open_jobs": open_jobs}


def write_schedule(agent, path: str, days: int = 21) -> dict[str, list[dict]]:
    rows = build_rows(agent, days)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    def sheet(name: str, cols: list[str], data: list[dict]) -> None:
        ws = wb.create_sheet(name)
        ws.append(cols)
        for r in data:
            ws.append([r.get(c, "") for c in cols])
        ws.freeze_panes = "A2"
        for i, col in enumerate(cols, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = max(10, len(col) + 2)

    sheet("Cleaners", CLEANER_COLS, rows["cleaners"])
    sheet("Schedule", SCHEDULE_COLS, rows["schedule"])
    sheet("Open jobs", OPEN_COLS, rows["open_jobs"])
    used = {"Cleaners", "Schedule", "Open jobs"}
    for c in rows["cleaners"]:
        base = re.sub(r"[\[\]:*?/\\]", "", c["name"])[:28] or f'c{c["cleaner_id"]}'
        name, n = base, 2
        while name in used:
            name, n = f"{base[:26]}-{n}", n + 1
        used.add(name)
        mine = [r for r in rows["schedule"] if r["cleaner_id"] == c["cleaner_id"]]
        sheet(name, [x for x in SCHEDULE_COLS if x not in ("cleaner_id", "cleaner")], mine)

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp.xlsx")
    wb.save(tmp)
    tmp.replace(p)
    return rows


def read_schedule(path: str) -> dict[str, list[dict]]:
    """Read the workbook back into rows. This is what Gemini's context is built from."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    out = {}
    for key, name in (("cleaners", "Cleaners"), ("schedule", "Schedule"), ("open_jobs", "Open jobs")):
        ws = wb[name]
        it = ws.iter_rows(values_only=True)
        head = [str(h) for h in next(it, ())]
        out[key] = [
            {h: ("" if v is None else v) for h, v in zip(head, row)} for row in it if any(v not in (None, "") for v in row)
        ]
    wb.close()
    return out
