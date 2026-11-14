"""The scheduling brain: calendar sync -> cleaning jobs -> offers to the closest cleaner -> replies."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import Settings
from .geo import haversine_km, normalize_phone
from .ical import Reservation, fetch_ics, parse_ics
from .loaders import load_cleaners, load_properties
from .messaging import Recipient, SendError
from .replies import parse_reply
from .templates import fmt_date, t

log = logging.getLogger(__name__)


def assignment_cost(km: float, held: int, penalty_km: float) -> float:
    """Lower is better. km = distance cleaner->room, held = upcoming jobs she already holds.

    cost = km * (1 + held) + penalty_km * held
    Nobody busy -> pure distance. Every job held adds penalty_km plus her distance again,
    so near cleaners still lead but get spread out, and far cleaners drop back fast once used.
    """
    return km * (1 + held) + penalty_km * held


def _mins(hhmm: str) -> int:
    h, _, m = (hhmm or "0:0").partition(":")
    return int(h) * 60 + int(m or 0)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class Agent:
    def __init__(self, settings: Settings, db, messenger, now_fn=None, fetch_fn=None):
        self.s = settings
        self.db = db
        self.messenger = messenger
        self.tz = ZoneInfo(settings.timezone)
        self.now = now_fn or (lambda: datetime.now(self.tz))
        self.fetch = fetch_fn or fetch_ics
        self.lock = asyncio.Lock()
        self._mtimes: dict[str, float | None] = {}

    # ---- small helpers -------------------------------------------------
    def q(self, sql, args=()):
        return self.db.execute(sql, args).fetchall()

    def q1(self, sql, args=()):
        return self.db.execute(sql, args).fetchone()

    def x(self, sql, args=()):
        return self.db.execute(sql, args)

    def _quiet(self, now: datetime) -> bool:
        s, e, h = self.s.quiet_start_hour, self.s.quiet_end_hour, now.hour
        if s == e:
            return False
        return (h >= s or h < e) if s > e else (s <= h < e)

    async def _send(self, cleaner, body: str, job_id: int | None = None) -> None:
        await self.messenger.send(Recipient(cleaner["phone"], cleaner["channel"], cleaner["viber_id"]), body, job_id)

    async def _safe_send(self, cleaner, body: str, job_id: int | None = None) -> None:
        try:
            await self._send(cleaner, body, job_id)
        except SendError as exc:
            log.error("could not message %s: %s", cleaner["name"], exc)

    async def _notify_owner(self, key: str, **kw) -> None:
        if not self.s.owner_phone:
            return
        body = t(self.s.owner_language, key, **kw)
        try:
            await self.messenger.send(Recipient(self.s.owner_phone, self.s.owner_channel), body, kw.get("id"))
        except SendError as exc:
            log.error("could not message owner: %s", exc)

    def _job_vars(self, job, prop, lang: str) -> dict:
        d = date.fromisoformat(job["checkout_date"])
        same_day = job["next_checkin_date"] == job["checkout_date"]
        return dict(
            id=job["id"], property=prop["name"], address=prop["address"] or "", date=fmt_date(d, lang),
            time=job["checkout_time"],
            turnover=t(lang, "stay") if job["kind"] == "stay" else t(lang, "turnover") if same_day else "",
        )

    # ---- data loading --------------------------------------------------
    async def reload_data(self, force: bool = False) -> None:
        """(Re)load the CSV files when they change on disk."""
        async with self.lock:
            for key, path, loader in (
                ("cleaners", self.s.cleaners_csv, load_cleaners),
                ("properties", self.s.properties_csv, load_properties),
            ):
                mtime = os.path.getmtime(path) if os.path.exists(path) else None
                if force or mtime != self._mtimes.get(key):
                    await loader(self.db, path, self.s)
                    self._mtimes[key] = mtime

    # ---- calendar sync -------------------------------------------------
    async def sync_calendars(self) -> None:
        async with self.lock:
            today = self.now().date()
            for prop in self.q("SELECT * FROM properties"):
                cals = self.q("SELECT * FROM calendars WHERE property_id=?", (prop["id"],))
                reservations, ok = [
                    Reservation(b["code"], date.fromisoformat(b["checkin_date"]), date.fromisoformat(b["checkout_date"]),
                                b["guest_name"] or "", "manual", b["checkout_time"])
                    for b in self.q("SELECT * FROM bookings WHERE property_id=?", (prop["id"],))
                ], True
                for cal in cals:
                    try:
                        text = await self.fetch(cal["url"])
                        reservations += parse_ics(text, cal["source"], self.s.ignore_airbnb_blocks)
                    except Exception as exc:
                        log.error("calendar %s (%s) failed: %s", cal["source"], prop["name"], exc)
                        ok = False
                if not ok:
                    # never cancel cleanings because of a failed download
                    continue
                await self._reconcile(prop, reservations, today)
            await self._advance()

    async def _reconcile(self, prop, reservations, today: date) -> None:
        horizon = today + timedelta(days=self.s.horizon_days)
        starts = sorted({r.start for r in reservations})
        wanted: dict[date, tuple[str, str]] = {}  # day -> (kind, time)
        for r in reservations:
            day = r.end
            if self.s.skip_sunday and day.weekday() == 6 and day not in starts:
                day += timedelta(days=1)  # nobody cleans on Sunday; do it Monday
            if today <= day <= horizon:
                wanted.setdefault(day, ("checkout", r.checkout_time or prop["checkout_time"]))
        every = self.s.stay_clean_every_days
        for r in reservations if every > 0 else ():  # mid-stay cleanings: every N nights while the guest is in
            day = r.start + timedelta(days=every)
            while day < r.end:
                d = day + timedelta(days=1) if self.s.skip_sunday and day.weekday() == 6 else day
                if d < r.end and today <= d <= horizon:
                    wanted.setdefault(d, ("stay", self.s.stay_clean_time))  # never replaces a checkout cleaning
                day += timedelta(days=every)
        now_iso = iso(self.now())

        active = {
            row["checkout_date"]: row
            for row in self.q(
                "SELECT * FROM jobs WHERE property_id=? AND status NOT IN ('cancelled','completed')", (prop["id"],)
            )
        }
        for checkout, (kind, out_time) in wanted.items():
            nxt = next((s for s in starts if s >= checkout), None) if kind == "checkout" else None
            nxt_iso = nxt.isoformat() if nxt else None
            existing = active.get(checkout.isoformat())
            if existing is None:
                self.x(
                    "INSERT INTO jobs(property_id, checkout_date, checkout_time, next_checkin_date, kind, status, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (prop["id"], checkout.isoformat(), out_time, nxt_iso, kind, now_iso, now_iso),
                )
                log.info("new %s cleaning job: %s on %s", kind, prop["name"], checkout)
            elif (existing["next_checkin_date"], existing["checkout_time"], existing["kind"]) != (nxt_iso, out_time, kind):
                self.x(
                    "UPDATE jobs SET next_checkin_date=?, checkout_time=?, kind=?, updated_at=? WHERE id=?",
                    (nxt_iso, out_time, kind, now_iso, existing["id"]),
                )
        for checkout_iso, job in active.items():
            if date.fromisoformat(checkout_iso) >= today and date.fromisoformat(checkout_iso) not in wanted:
                if date.fromisoformat(checkout_iso) <= horizon:
                    await self._cancel_job(job, notify=True)

    async def _cancel_job(self, job, notify: bool) -> None:
        prop = self.q1("SELECT * FROM properties WHERE id=?", (job["property_id"],))
        to_tell = []
        for off in self.q("SELECT * FROM offers WHERE job_id=? AND status='pending'", (job["id"],)):
            self.x("UPDATE offers SET status='cancelled', responded_at=? WHERE id=?", (iso(self.now()), off["id"]))
            to_tell.append(self.q1("SELECT * FROM cleaners WHERE id=?", (off["cleaner_id"],)))
        assigned = None
        if job["status"] == "assigned" and job["cleaner_id"]:
            assigned = self.q1("SELECT * FROM cleaners WHERE id=?", (job["cleaner_id"],))
            to_tell.append(assigned)
        self.x("UPDATE jobs SET status='cancelled', updated_at=? WHERE id=?", (iso(self.now()), job["id"]))
        log.info("cancelled job %s (%s)", job["id"], prop["name"] if prop else "?")
        if not notify:
            return
        for c in to_tell:
            lang = c["language"]
            await self._safe_send(c, t(lang, "cancelled", **self._job_vars(job, prop, lang)), job["id"])
        if assigned is not None:
            v = self._job_vars(job, prop, self.s.owner_language)
            await self._notify_owner("owner_cancelled", cleaner=assigned["name"], **v)

    # ---- offers --------------------------------------------------------
    async def advance(self) -> None:
        async with self.lock:
            await self._advance()

    async def _advance(self) -> None:
        now = self.now()
        today = now.date()

        for job in self.q(
            "SELECT * FROM jobs WHERE status IN ('pending','offering','unfilled') AND checkout_date < ?",
            (today.isoformat(),),
        ):
            await self._cancel_job(job, notify=False)

        for off in self.q("SELECT * FROM offers WHERE status='pending' AND expires_at <= ?", (iso(now),)):
            self.x("UPDATE offers SET status='expired', responded_at=? WHERE id=?", (iso(now), off["id"]))
            self.x("UPDATE jobs SET status='pending', updated_at=? WHERE id=? AND status='offering'", (iso(now), off["job_id"]))
            log.info("offer %s expired", off["id"])

        if self._quiet(now):
            return
        horizon = (today + timedelta(days=self.s.offer_lead_days)).isoformat()
        for job in self.q(
            "SELECT * FROM jobs WHERE status='pending' AND checkout_date <= ? ORDER BY checkout_date, id", (horizon,)
        ):
            await self._offer_next(job)

    def _held(self) -> dict[int, list[tuple[str, int]]]:
        """cleaner_id -> [(date, start minute)] for upcoming jobs she has accepted or been offered and not answered."""
        held: dict[int, list[tuple[str, int]]] = {}
        for r in self.q(
            """SELECT COALESCE(j.cleaner_id, o.cleaner_id) AS cid, j.checkout_date, j.checkout_time
               FROM jobs j LEFT JOIN offers o ON o.job_id=j.id AND o.status='pending'
               WHERE j.checkout_date >= ? AND (j.status IN ('assigned','completed') OR o.id IS NOT NULL)""",
            (self.now().date().isoformat(),),
        ):
            held.setdefault(r["cid"], []).append((r["checkout_date"], _mins(r["checkout_time"])))
        return held

    def _clash(self, cleaner, job, slots) -> bool:
        """Daily cap reached, or another job starts within cleaning_min of this one (can't be in two rooms at once)."""
        # ponytail: jobs keep their checkout time; no sequencing (room 4 at 11:00, room 5 at 12:00). Add a scheduled start if needed.
        day = [m for d, m in slots if d == job["checkout_date"]]
        start = _mins(job["checkout_time"])
        return len(day) >= cleaner["max_jobs_per_day"] or any(abs(m - start) < self.s.cleaning_min for m in day)

    def _candidates(self, job, prop, planned: dict | None = None) -> list:
        """Cleaners who could take this job, cheapest first (assignment_cost), as (km, cleaner) pairs.

        planned: extra hypothetical holdings {cleaner_id: [(date, minute)]}, used by the UI preview."""
        excluded = {
            r["cleaner_id"]
            for r in self.q(
                "SELECT cleaner_id FROM offers WHERE job_id=? AND status IN ('pending','accepted','declined','expired','failed')",
                (job["id"],),
            )
        }
        held = self._held()
        scored = []
        for c in self.q("SELECT * FROM cleaners WHERE active=1"):
            if c["id"] in excluded:
                continue
            slots = held.get(c["id"], []) + (planned or {}).get(c["id"], [])
            if self._clash(c, job, slots):
                continue
            km = haversine_km(prop["lat"], prop["lon"], c["lat"], c["lon"])
            if self.s.max_distance_km and km > self.s.max_distance_km:
                continue
            scored.append((assignment_cost(km, len(slots), self.s.load_penalty_km), km, c))
        scored.sort(key=lambda x: (x[0], x[2]["name"]))
        return [(km, c) for _, km, c in scored]

    async def _offer_next(self, job) -> None:
        prop = self.q1("SELECT * FROM properties WHERE id=?", (job["property_id"],))
        candidates = self._candidates(job, prop)
        now = self.now()
        for km, c in candidates:
            cur = self.x(
                "INSERT INTO offers(job_id, cleaner_id, status, distance_km, sent_at, expires_at) VALUES (?, ?, 'pending', ?, ?, ?)",
                (job["id"], c["id"], km, iso(now), iso(now + timedelta(minutes=self.s.offer_timeout_min))),
            )
            lang = c["language"]
            body = t(lang, "offer", name=c["name"], km=f"{km:.1f}", **self._job_vars(job, prop, lang))
            try:
                await self._send(c, body, job["id"])
            except SendError as exc:
                log.error("offer to %s failed: %s", c["name"], exc)
                self.x("UPDATE offers SET status='failed' WHERE id=?", (cur.lastrowid,))
                continue
            self.x("UPDATE jobs SET status='offering', updated_at=? WHERE id=?", (iso(now), job["id"]))
            log.info("offered job %s to %s (%.1f km)", job["id"], c["name"], km)
            return

        self.x("UPDATE jobs SET status='unfilled', updated_at=? WHERE id=?", (iso(now), job["id"]))
        log.warning("no cleaner available for job %s", job["id"])
        await self._notify_owner("owner_unfilled", **self._job_vars(job, prop, self.s.owner_language))

    async def retry_job(self, job_id: int) -> bool:
        """Owner added cleaners / changed their mind: start a fresh round for an unfilled job."""
        async with self.lock:
            job = self.q1("SELECT * FROM jobs WHERE id=?", (job_id,))
            if job is None or job["status"] not in ("unfilled", "pending"):
                return False
            self.x("UPDATE offers SET status='retired' WHERE job_id=? AND status IN ('declined','expired','failed')", (job_id,))
            self.x("UPDATE jobs SET status='pending', updated_at=? WHERE id=?", (iso(self.now()), job_id))
            await self._advance()
            return True

    # ---- inbound texts -------------------------------------------------
    async def handle_inbound(self, channel: str, sender: str, text: str) -> None:
        async with self.lock:
            try:
                self.messenger.log_inbound(channel, sender, text)
            except Exception:  # logging must never block handling
                log.exception("could not log inbound message")
            await self._inbound(channel, sender, text)

    async def _inbound(self, channel: str, sender: str, text: str) -> None:
        cc = self.s.default_country_code
        if channel == "viber":
            cleaner = self.q1("SELECT * FROM cleaners WHERE viber_id=? AND active=1", (sender,))
            if cleaner is None:
                await self._link_viber(sender, text)
                return
        else:
            cleaner = self.q1("SELECT * FROM cleaners WHERE phone=? AND active=1", (normalize_phone(sender, cc),))
            if cleaner is None:
                log.info("ignoring text from unknown number %s", sender)
                return

        lang = cleaner["language"]
        intent, job_id = parse_reply(text)
        if intent in ("yes", "no"):
            await self._answer_offer(cleaner, intent, job_id)
        elif intent == "done":
            await self._mark_done(cleaner, job_id)
        else:
            await self._safe_send(cleaner, t(lang, "unknown"))

    async def _link_viber(self, viber_id: str, text: str) -> None:
        """Viber users are identified by an opaque id; match it to a cleaner via their phone number."""
        rcpt = Recipient("", "viber", viber_id)
        if len(re.sub(r"\D", "", text)) >= 7:
            phone = normalize_phone(text, self.s.default_country_code)
            cleaner = self.q1("SELECT * FROM cleaners WHERE phone=? AND active=1", (phone,))
            if cleaner is not None:
                self.x("UPDATE cleaners SET viber_id=? WHERE id=?", (viber_id, cleaner["id"]))
                try:
                    await self.messenger.send(rcpt, t(cleaner["language"], "linked", name=cleaner["name"]))
                except SendError as exc:
                    log.error("viber link reply failed: %s", exc)
                return
        try:
            await self.messenger.send(rcpt, t("en", "link_prompt"))
        except SendError as exc:
            log.error("viber prompt failed: %s", exc)

    def _find_offer(self, cleaner_id: int, job_id: int | None):
        """Return (offer, reason). reason is 'none' or 'ambiguous' when offer is None."""
        if job_id is not None:
            off = self.q1(
                "SELECT * FROM offers WHERE job_id=? AND cleaner_id=? ORDER BY id DESC LIMIT 1", (job_id, cleaner_id)
            )
            return (off, "none") if off else (None, "none")
        pending = self.q("SELECT * FROM offers WHERE cleaner_id=? AND status='pending' ORDER BY id", (cleaner_id,))
        if len(pending) == 1:
            return pending[0], ""
        if len(pending) > 1:
            return None, "ambiguous"
        late = self.q(
            "SELECT o.* FROM offers o JOIN jobs j ON j.id=o.job_id "
            "WHERE o.cleaner_id=? AND o.status='expired' AND j.status IN ('pending','unfilled') ORDER BY o.id DESC",
            (cleaner_id,),
        )
        return (late[0], "") if len(late) == 1 else (None, "none")

    async def _answer_offer(self, cleaner, intent: str, job_id: int | None) -> None:
        lang = cleaner["language"]
        offer, reason = self._find_offer(cleaner["id"], job_id)
        if offer is None:
            if reason == "ambiguous":
                first = self.q1("SELECT job_id FROM offers WHERE cleaner_id=? AND status='pending' ORDER BY id", (cleaner["id"],))
                await self._safe_send(cleaner, t(lang, "ambiguous", id=first["job_id"]))
            else:
                await self._safe_send(cleaner, t(lang, "no_open"))
            return

        job = self.q1("SELECT * FROM jobs WHERE id=?", (offer["job_id"],))
        prop = self.q1("SELECT * FROM properties WHERE id=?", (job["property_id"],))
        v = self._job_vars(job, prop, lang)
        now_iso = iso(self.now())

        if intent == "no":
            if offer["status"] in ("pending", "expired"):
                self.x("UPDATE offers SET status='declined', responded_at=? WHERE id=?", (now_iso, offer["id"]))
                if offer["status"] == "pending" and job["status"] == "offering":
                    self.x("UPDATE jobs SET status='pending', updated_at=? WHERE id=?", (now_iso, job["id"]))
            await self._safe_send(cleaner, t(lang, "declined"), job["id"])
            await self._advance()
            return

        # intent == "yes"
        if job["status"] == "cancelled":
            await self._safe_send(cleaner, t(lang, "cancelled", **v), job["id"])
            return
        available = job["status"] in ("offering", "pending", "unfilled") and (
            offer["status"] == "pending" or (offer["status"] == "expired" and job["status"] in ("pending", "unfilled"))
        )
        if not available:
            await self._safe_send(cleaner, t(lang, "taken", **v), job["id"])
            return

        accepted = [
            (r["checkout_date"], _mins(r["checkout_time"]))
            for r in self.q("SELECT checkout_date, checkout_time FROM jobs WHERE cleaner_id=? AND status='assigned' AND id!=?",
                            (cleaner["id"], job["id"]))
        ]
        if self._clash(cleaner, job, accepted):
            self.x("UPDATE offers SET status='declined', responded_at=? WHERE id=?", (now_iso, offer["id"]))
            self.x("UPDATE jobs SET status='pending', updated_at=? WHERE id=?", (now_iso, job["id"]))
            await self._safe_send(cleaner, t(lang, "full", **v), job["id"])
            await self._advance()
            return

        self.x("UPDATE offers SET status='accepted', responded_at=? WHERE id=?", (now_iso, offer["id"]))
        self.x("UPDATE jobs SET status='assigned', cleaner_id=?, updated_at=? WHERE id=?", (cleaner["id"], now_iso, job["id"]))
        self.x("UPDATE offers SET status='cancelled', responded_at=? WHERE job_id=? AND status='pending'", (now_iso, job["id"]))
        await self._safe_send(cleaner, t(lang, "confirmed", **v), job["id"])
        await self._notify_owner("owner_assigned", cleaner=cleaner["name"], **self._job_vars(job, prop, self.s.owner_language))

    async def _mark_done(self, cleaner, job_id: int | None) -> None:
        lang = cleaner["language"]
        today = self.now().date().isoformat()
        if job_id is not None:
            job = self.q1("SELECT * FROM jobs WHERE id=? AND cleaner_id=? AND status='assigned'", (job_id, cleaner["id"]))
        else:
            rows = self.q(
                "SELECT * FROM jobs WHERE cleaner_id=? AND status='assigned' AND checkout_date<=? ORDER BY checkout_date",
                (cleaner["id"], today),
            )
            job = rows[0] if len(rows) == 1 else None
            if len(rows) > 1:
                await self._safe_send(cleaner, t(lang, "ambiguous", id=rows[0]["id"]))
                return
        if job is None:
            await self._safe_send(cleaner, t(lang, "done_unknown"))
            return
        prop = self.q1("SELECT * FROM properties WHERE id=?", (job["property_id"],))
        self.x("UPDATE jobs SET status='completed', updated_at=? WHERE id=?", (iso(self.now()), job["id"]))
        await self._safe_send(cleaner, t(lang, "done_ack", id=job["id"]), job["id"])
        await self._notify_owner("owner_done", cleaner=cleaner["name"], **self._job_vars(job, prop, self.s.owner_language))

    # ---- reminders -----------------------------------------------------
    async def send_reminders(self) -> None:
        async with self.lock:
            now = self.now()
            if self._quiet(now) or now.hour < self.s.reminder_hour:
                return
            tomorrow = (now.date() + timedelta(days=1)).isoformat()
            for job in self.q(
                "SELECT * FROM jobs WHERE status='assigned' AND checkout_date=? AND reminded_at IS NULL", (tomorrow,)
            ):
                cleaner = self.q1("SELECT * FROM cleaners WHERE id=?", (job["cleaner_id"],))
                prop = self.q1("SELECT * FROM properties WHERE id=?", (job["property_id"],))
                lang = cleaner["language"]
                await self._safe_send(cleaner, t(lang, "reminder", **self._job_vars(job, prop, lang)), job["id"])
                self.x("UPDATE jobs SET reminded_at=? WHERE id=?", (iso(now), job["id"]))

    # ---- reporting -----------------------------------------------------
    def list_jobs(self) -> list[dict]:
        rows = self.q(
            """SELECT j.id, j.property_id, p.name AS property, j.checkout_date, j.checkout_time, j.next_checkin_date, j.kind,
                      j.status, c.name AS cleaner, j.cleaner_id,
                      oc.id AS offered_id, oc.name AS offered_to, o.distance_km AS offered_km
               FROM jobs j
               LEFT JOIN offers o ON o.job_id=j.id AND o.status='pending'
               LEFT JOIN cleaners oc ON oc.id=o.cleaner_id JOIN properties p ON p.id=j.property_id
               LEFT JOIN cleaners c ON c.id=j.cleaner_id
               ORDER BY j.checkout_date, j.id"""
        )
        out = [dict(r) for r in rows]
        planned: dict[int, list] = {}
        for j in out:  # not asked yet: show who would be asked first (planned in order, like _advance), flagged as queued
            if j["status"] == "pending" and j["offered_to"] is None:
                job = self.q1("SELECT * FROM jobs WHERE id=?", (j["id"],))
                best = self._candidates(job, self.q1("SELECT * FROM properties WHERE id=?", (j["property_id"],)), planned)
                if best:
                    km, c = best[0]
                    j["offered_id"], j["offered_to"], j["offered_km"], j["queued"] = c["id"], c["name"], km, True
                    planned.setdefault(c["id"], []).append((job["checkout_date"], _mins(job["checkout_time"])))
        return out

    async def tick(self, sync: bool) -> None:
        """One pass of the background loop."""
        await self.reload_data()
        if sync:
            await self.sync_calendars()
        else:
            await self.advance()
        await self.send_reminders()
