"""Telegram channel + Gemini hooks + the per-cleaner Excel. No network: Gemini is a fake client."""
from __future__ import annotations

import asyncio
from datetime import timedelta

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import connect
from app.main import create_app
from app.messaging import Messenger, Recipient
from app.schedule_xlsx import read_schedule

from .conftest import TODAY, Env, ics
from .test_flow import MARIA, feed_with_checkout, run


class FakeLLM:
    def __init__(self, reply=None, choose=None, boom=False):
        self.reply, self.choose, self.boom = reply, choose, boom
        self.choose_calls, self.reply_calls = [], []

    async def interpret_reply(self, text, context):
        self.reply_calls.append((text, context))
        if self.boom:
            raise RuntimeError("Gemini down")
        return {"intent": "other", "job_id": None, "unavailable_from": None, "unavailable_to": None,
                "reply_text": "", **(self.reply or {})}

    async def choose_cleaner(self, job, candidates, context):
        self.choose_calls.append((job, candidates, context))
        if self.boom:
            raise RuntimeError("Gemini down")
        return self.choose


def telegram_env(llm=None):
    e = Env(llm=llm)
    e.add_property("plaka", "Plaka Studio", 37.9715, 23.7300, {"airbnb": "airbnb://plaka"})
    e.add_cleaner("Maria", MARIA, 37.9755, 23.7348, channel="telegram", telegram_id="tg-maria")
    e.add_cleaner("Nikos", "+306900000002", 38.0735, 23.8118)
    e.add_cleaner("Elena", "+306900000003", 37.8650, 23.7530)
    return e


def offered_to(e):
    return [e.db.execute("SELECT name FROM cleaners WHERE id=?", (r["cleaner_id"],)).fetchone()["name"]
            for r in e.db.execute("SELECT cleaner_id FROM offers ORDER BY id")]


# ---- Part 1: Telegram ---------------------------------------------------
def test_dry_run_send_logs_a_telegram_message():
    db = connect(":memory:")
    asyncio.run(Messenger(Settings(dry_run=True), db).send(Recipient("+30690", "telegram", telegram_id="42"), "hi", 7))
    row = db.execute("SELECT channel, party, provider FROM messages").fetchone()
    assert (row["channel"], row["party"], row["provider"]) == ("telegram", "42", "dry-run")


def test_telegram_without_chat_id_falls_back_to_sms():
    db = connect(":memory:")
    asyncio.run(Messenger(Settings(dry_run=True), db).send(Recipient("+30690", "telegram"), "hi"))
    assert db.execute("SELECT channel FROM messages").fetchone()["channel"] == "sms"


def test_offer_goes_to_telegram_and_yes_assigns():
    e = telegram_env()
    feed_with_checkout(e)
    run(e.agent.sync_calendars())
    assert any("YES 1" in b for b in e.messenger.to("tg-maria"))
    run(e.agent.handle_inbound("telegram", "tg-maria", "yes"))
    assert e.agent.list_jobs()[0]["status"] == "assigned"


def test_link_flow_matches_phone_and_saves_telegram_id():
    e = telegram_env()
    e.db.execute("UPDATE cleaners SET telegram_id=NULL")
    run(e.agent.handle_inbound("telegram", "555", "/start"))
    assert "phone number" in e.messenger.to("555")[0]  # chat is unknown: asked for the phone
    run(e.agent.handle_inbound("telegram", "555", "6900000001"))
    assert e.db.execute("SELECT telegram_id FROM cleaners WHERE name='Maria'").fetchone()[0] == "555"
    assert "linked" in e.messenger.to("555")[-1]


def make_client(env, **over):
    for k, v in over.items():
        setattr(env.settings, k, v)
    return TestClient(create_app(env.settings, agent=env.agent, background=False))


UPDATE = {"update_id": 1, "message": {"chat": {"id": 123, "type": "private"}, "from": {"id": 123}, "text": "yes"}}


def test_webhook_secret_accepted_and_rejected():
    e = telegram_env()
    feed_with_checkout(e)
    run(e.agent.sync_calendars())
    e.db.execute("UPDATE cleaners SET telegram_id='123' WHERE name='Maria'")
    with make_client(e, telegram_webhook_secret="s3cret", dry_run=False) as c:
        assert c.post("/webhooks/telegram", json=UPDATE).status_code == 403
        assert c.post("/webhooks/telegram", json=UPDATE, headers={"X-Telegram-Bot-Api-Secret-Token": "nope"}).status_code == 403
        assert e.agent.list_jobs()[0]["status"] == "offering"
        ok = c.post("/webhooks/telegram", json=UPDATE, headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"})
        assert ok.status_code == 200
    assert e.agent.list_jobs()[0]["status"] == "assigned"  # background task ran: "yes" accepted the offer


def test_add_cleaner_with_channel_via_api(monkeypatch):
    async def fake_geocode(db, address):
        return (37.97, 23.73)

    monkeypatch.setattr("app.api.geocode", fake_geocode)
    e = Env()
    with make_client(e) as c:
        r = c.post("/api/cleaners", json={"name": "Anna", "phone": "6911111111", "address": "x", "channel": "telegram"})
    assert r.status_code == 200
    assert e.db.execute("SELECT channel FROM cleaners WHERE name='Anna'").fetchone()[0] == "telegram"


# ---- Excel ------------------------------------------------------------
def test_excel_lists_each_cleaners_program_from_the_db():
    e = telegram_env()
    feed_with_checkout(e)
    run(e.agent.sync_calendars())
    run(e.agent.handle_inbound("telegram", "tg-maria", "yes"))
    sheets = e.agent.export_schedule()
    assert sheets == read_schedule(e.settings.schedule_xlsx)
    row = sheets["schedule"][0]
    assert (row["cleaner"], row["status"], row["job_id"]) == ("Maria", "assigned", 1)
    assert {c["name"] for c in sheets["cleaners"]} == {"Maria", "Nikos", "Elena"}


# ---- Part 2: Gemini -----------------------------------------------------
def test_without_gemini_unknown_text_gets_the_help_text():
    e = telegram_env()
    feed_with_checkout(e)
    run(e.agent.sync_calendars())
    run(e.agent.handle_inbound("telegram", "tg-maria", "hmm maybe"))
    assert "didn't understand" in e.messenger.to("tg-maria")[-1]


def test_sick_until_friday_stores_dates_and_job_goes_to_next_cleaner():
    until = (TODAY + timedelta(days=5)).isoformat()
    llm = FakeLLM(reply={"intent": "unavailable", "unavailable_from": TODAY.isoformat(), "unavailable_to": until,
                         "reply_text": "Get well soon, we'll arrange it."})
    e = telegram_env(llm)
    feed_with_checkout(e)
    run(e.agent.sync_calendars())
    assert offered_to(e) == ["Maria"]
    run(e.agent.handle_inbound("telegram", "tg-maria", "I'm ill, can't work until Friday"))
    u = e.db.execute("SELECT * FROM unavailability").fetchone()
    assert (u["from_date"], u["to_date"]) == (TODAY.isoformat(), until)
    assert offered_to(e)[1] != "Maria" and len(offered_to(e)) == 2
    assert e.messenger.to("tg-maria")[-1] == "Get well soon, we'll arrange it."
    assert e.agent.list_jobs()[0]["status"] == "offering"
    ctx = llm.reply_calls[0][1]  # Gemini saw her open offer
    assert ctx["open_offers"][0]["job_id"] == 1


def test_unavailable_cleaner_is_skipped_even_without_gemini():
    e = telegram_env()
    e.db.execute("INSERT INTO unavailability(cleaner_id, from_date, to_date) VALUES (1, ?, ?)",
                 (TODAY.isoformat(), (TODAY + timedelta(days=10)).isoformat()))
    feed_with_checkout(e)
    run(e.agent.sync_calendars())
    assert offered_to(e) == ["Elena"] or offered_to(e) == ["Nikos"]


def test_sick_after_accepting_releases_the_job_and_reassigns():
    llm = FakeLLM(reply={"intent": "unavailable", "unavailable_from": TODAY.isoformat(),
                         "unavailable_to": (TODAY + timedelta(days=4)).isoformat(), "reply_text": "OK thanks."})
    e = telegram_env(llm)
    feed_with_checkout(e)
    run(e.agent.sync_calendars())
    run(e.agent.handle_inbound("telegram", "tg-maria", "yes"))
    run(e.agent.handle_inbound("telegram", "tg-maria", "ugh, flu"))
    j = e.agent.list_jobs()[0]
    assert j["status"] == "offering" and j["cleaner"] is None and j["offered_to"] != "Maria"


def test_free_text_decline_with_gemini_offers_next_cleaner():
    llm = FakeLLM(reply={"intent": "no", "job_id": 1, "reply_text": "ok"})
    e = telegram_env(llm)
    feed_with_checkout(e)
    run(e.agent.sync_calendars())
    run(e.agent.handle_inbound("telegram", "tg-maria", "only after 14:00 sorry"))
    assert offered_to(e)[0] == "Maria" and len(offered_to(e)) == 2
    assert e.db.execute("SELECT status FROM offers WHERE id=1").fetchone()[0] == "declined"


def test_llm_error_on_reply_falls_back_to_help_text():
    e = telegram_env(FakeLLM(boom=True))
    feed_with_checkout(e)
    run(e.agent.sync_calendars())
    run(e.agent.handle_inbound("telegram", "tg-maria", "dunno"))
    assert "didn't understand" in e.messenger.to("tg-maria")[-1]


def test_invented_job_id_is_ignored():
    llm = FakeLLM(reply={"intent": "yes", "job_id": 999, "reply_text": ""})
    e = telegram_env(llm)
    feed_with_checkout(e)
    run(e.agent.sync_calendars())
    run(e.agent.handle_inbound("telegram", "tg-maria", "sounds fine to me"))  # parse_reply: unknown
    assert e.agent.list_jobs()[0]["status"] == "assigned"  # 999 ignored, her single open offer is used


def _two_candidates(llm):
    e = telegram_env(llm)
    feed_with_checkout(e)
    return e


def test_choose_cleaner_pick_goes_first():
    e = _two_candidates(FakeLLM(choose=3))  # Elena
    e.db.execute("UPDATE cleaners SET channel='sms'")
    run(e.agent.sync_calendars())
    assert offered_to(e) == ["Elena"]
    job, cands, ctx = e.agent.llm.choose_calls[0]
    assert [c["name"] for c in cands][0] == "Maria" and "schedule" in cands[0] and job["hotel"] == "Plaka Studio"


def test_choose_cleaner_invalid_id_uses_nearest():
    e = _two_candidates(FakeLLM(choose=999))
    run(e.agent.sync_calendars())
    assert offered_to(e) == ["Maria"]


def test_choose_cleaner_exception_uses_nearest():
    e = _two_candidates(FakeLLM(boom=True))
    run(e.agent.sync_calendars())
    assert offered_to(e) == ["Maria"]


def test_gemini_pick_is_offered_only_once():
    e = _two_candidates(FakeLLM(choose=1))
    e.messenger.fail_for = {MARIA}
    run(e.agent.sync_calendars())
    assert offered_to(e)[:2] == ["Maria", "Elena"] or offered_to(e)[:2] == ["Maria", "Nikos"]


def test_unreachable_cleaners_are_skipped_when_live():
    e = telegram_env()
    e.settings.dry_run, e.settings.telegram_bot_token = False, "tok"  # no Twilio: SMS cleaners can't be reached
    feed_with_checkout(e)
    run(e.agent.sync_calendars())
    assert offered_to(e) == ["Maria"]


def test_linking_retries_unfilled_jobs():
    e = telegram_env()
    e.settings.dry_run, e.settings.telegram_bot_token = False, "tok"
    e.db.execute("UPDATE cleaners SET telegram_id=NULL")
    feed_with_checkout(e)
    run(e.agent.sync_calendars())
    assert e.agent.list_jobs()[0]["status"] == "unfilled"  # nobody reachable yet
    run(e.agent.handle_inbound("telegram", "tg-new", "6900000001"))
    assert e.agent.list_jobs()[0]["status"] == "offering" and offered_to(e) == ["Maria"]
    assert any("YES 1" in b for b in e.messenger.to("tg-new"))


# ---- one-tap link: t.me/<bot>?start=<code> ---------------------------------
def test_one_tap_link_links_the_chat_and_is_single_use():
    e = telegram_env()
    e.db.execute("UPDATE cleaners SET telegram_id=NULL")
    code = e.agent.telegram_link_token(1)
    assert code == e.agent.telegram_link_token(1)  # stable until used
    run(e.agent.handle_inbound("telegram", "tg-phone", f"/start {code}"))
    assert e.db.execute("SELECT telegram_id FROM cleaners WHERE id=1").fetchone()[0] == "tg-phone"
    assert "linked" in e.messenger.to("tg-phone")[0]
    run(e.agent.handle_inbound("telegram", "tg-stranger", f"/start {code}"))  # forwarded link: dead now
    assert e.db.execute("SELECT telegram_id FROM cleaners WHERE id=1").fetchone()[0] == "tg-phone"
    assert "already used" in e.messenger.to("tg-stranger")[0] or "χρησιμοποιηθεί" in e.messenger.to("tg-stranger")[0]
    assert e.agent.telegram_link_token(1) != code  # a fresh one is made on demand


def test_one_tap_link_moves_cleaner_to_a_new_phone_and_sends_offers():
    e = telegram_env()
    e.settings.dry_run, e.settings.telegram_bot_token = False, "tok"
    feed_with_checkout(e)
    e.db.execute("UPDATE cleaners SET telegram_id=NULL")
    run(e.agent.sync_calendars())
    assert e.agent.list_jobs()[0]["status"] == "unfilled"
    run(e.agent.handle_inbound("telegram", "tg-new", f"/start {e.agent.telegram_link_token(1)}"))
    assert any("YES 1" in b for b in e.messenger.to("tg-new"))


def test_telegram_link_endpoint():
    e = telegram_env()
    with make_client(e, telegram_bot_token="tok", telegram_bot_username="Test_bot") as c:
        r = c.get("/api/cleaners/1/telegram-link").json()
        assert r["url"].startswith("https://t.me/Test_bot?start=") and r["linked"] is True and r["name"] == "Maria"
        assert c.get("/api/cleaners/999/telegram-link").status_code == 404
    with make_client(Env(), telegram_bot_token="") as c:
        assert c.get("/api/cleaners/1/telegram-link").status_code == 422


# ---- emailing the link --------------------------------------------------------
class FakeMailer:
    configured = True

    def __init__(self):
        self.sent = []

    async def send(self, to, subject, text, html=None):
        self.sent.append((to, subject, text, html))


def test_email_the_one_tap_link():
    e = telegram_env()
    e.agent.mailer = FakeMailer()
    e.db.execute("UPDATE cleaners SET email='maria@example.com' WHERE id=1")
    with make_client(e, telegram_bot_token="tok", telegram_bot_username="Test_bot") as c:
        assert c.get("/api/cleaners/1/telegram-link").json()["can_email"] is True
        r = c.post("/api/cleaners/1/telegram-link/email")
        assert r.status_code == 200 and r.json()["to"] == "maria@example.com"
        assert c.post("/api/cleaners/2/telegram-link/email").status_code == 422  # Nikos has no email
    to, subject, text, html = e.agent.mailer.sent[0]
    code = e.db.execute("SELECT link_token FROM cleaners WHERE id=1").fetchone()[0]
    assert to == "maria@example.com" and f"https://t.me/Test_bot?start={code}" in text and code in html


def test_email_not_configured_is_a_clear_error():
    e = telegram_env()
    e.db.execute("UPDATE cleaners SET email='maria@example.com' WHERE id=1")
    with make_client(e, telegram_bot_token="tok", telegram_bot_username="Test_bot", smtp_user="", smtp_password="") as c:
        r = c.post("/api/cleaners/1/telegram-link/email")
    assert r.status_code == 422 and "SMTP" in r.json()["detail"]


def test_new_telegram_cleaner_gets_the_link_by_email(monkeypatch):
    async def fake_geocode(db, address):
        return (37.97, 23.73)

    monkeypatch.setattr("app.api.geocode", fake_geocode)
    e = Env()
    e.agent.mailer = FakeMailer()
    with make_client(e, telegram_bot_token="tok", telegram_bot_username="Test_bot") as c:
        r = c.post("/api/cleaners", json={"name": "Anna K", "phone": "6911111111", "address": "x",
                                          "channel": "telegram", "email": "anna@example.com"}).json()
        assert r["emailed"] == "anna@example.com" and e.agent.mailer.sent[0][0] == "anna@example.com"
        assert c.post("/api/cleaners", json={"name": "B", "phone": "6922222222", "address": "x",
                                             "email": "not-an-email"}).status_code == 422


def test_csv_email_column_is_loaded(tmp_path):
    from app.loaders import load_cleaners
    f = tmp_path / "c.csv"
    f.write_text("name,phone,lat,lon,channel,email\nAnna,+306911111111,37.9,23.7,telegram,anna@example.com\n")
    e = Env()
    run(load_cleaners(e.db, str(f), e.settings))
    assert e.db.execute("SELECT email FROM cleaners WHERE name='Anna'").fetchone()[0] == "anna@example.com"


def test_ask_for_offers_resends_them_and_lists_confirmed_jobs():
    e = telegram_env(FakeLLM(reply={"intent": "status", "reply_text": "I'm sending them right away"}))
    feed_with_checkout(e)
    run(e.agent.sync_calendars())
    n = len(e.messenger.to("tg-maria"))
    run(e.agent.handle_inbound("telegram", "tg-maria", "send me the offers please"))
    sent = e.messenger.to("tg-maria")[n:]
    assert len(sent) == 1 and "YES 1" in sent[0]  # the real offer again, not Gemini's promise
    run(e.agent.handle_inbound("telegram", "tg-maria", "yes"))
    run(e.agent.handle_inbound("telegram", "tg-maria", "what is my programme?"))
    assert any("#1" in b and "Plaka Studio" in b for b in e.messenger.to("tg-maria")[-1:])


def test_status_with_nothing_open_says_so():
    e = telegram_env(FakeLLM(reply={"intent": "status"}))
    run(e.agent.handle_inbound("telegram", "tg-maria", "show my jobs"))
    assert "no open offers" in e.messenger.to("tg-maria")[-1]


def _two_jobs_for_maria(llm):
    e = telegram_env(llm)
    e.add_property("agora", "Agora Flat", 37.9720, 23.7310, {"airbnb": "airbnb://agora"})
    feed_with_checkout(e, days_ahead=4)
    e.feeds["airbnb://agora"] = ics(("r9", TODAY, TODAY + timedelta(days=3), "Reserved"))
    run(e.agent.sync_calendars())
    return e


def test_one_message_accepts_one_offer_and_refuses_the_rest():
    llm = FakeLLM(reply={"intent": "answers", "decisions": [{"job_id": 2, "answer": "yes"}], "decline_rest": True,
                         "reply_text": "ok"})
    e = _two_jobs_for_maria(llm)
    assert [j["offered_to"] for j in e.agent.list_jobs()] == ["Maria", "Maria"]
    run(e.agent.handle_inbound("telegram", "tg-maria", "εγώ δεν μπορώ καθόλου, μόνο την 2 μπορώ"))
    jobs_ = {j["id"]: j for j in e.agent.list_jobs()}
    assert jobs_[2]["status"] == "assigned" and jobs_[2]["cleaner"] == "Maria"
    assert jobs_[1]["status"] == "offering" and jobs_[1]["offered_to"] != "Maria"  # next cleaner was asked
    mine = e.messenger.to("tg-maria")
    assert any("Confirmed" in b for b in mine) and any("#1" in b and "can't do" in b for b in mine)
    ctx = llm.reply_calls[0][1]  # Gemini saw both open offers with room/date, so "2" can be matched
    assert {o["job_id"] for o in ctx["open_offers"]} == {1, 2}


def test_batch_answers_ignore_ids_that_are_not_her_open_offers():
    llm = FakeLLM(reply={"intent": "answers", "decisions": [{"job_id": 77, "answer": "yes"}], "decline_rest": False})
    e = _two_jobs_for_maria(llm)
    run(e.agent.handle_inbound("telegram", "tg-maria", "ναι στην 77"))
    assert all(j["status"] == "offering" and j["offered_to"] == "Maria" for j in e.agent.list_jobs())


def test_unmentioned_offers_stay_open_without_decline_rest():
    llm = FakeLLM(reply={"intent": "answers", "decisions": [{"job_id": 1, "answer": "no"}], "decline_rest": False})
    e = _two_jobs_for_maria(llm)
    run(e.agent.handle_inbound("telegram", "tg-maria", "όχι την 1"))
    jobs_ = {j["id"]: j for j in e.agent.list_jobs()}
    assert jobs_[2]["offered_to"] == "Maria" and jobs_[1]["offered_to"] != "Maria"
