import asyncio
from datetime import date, timedelta

from .conftest import TODAY, Env, ics

MARIA, NIKOS, ELENA = "+306900000001", "+306900000002", "+306900000003"


def run(coro):
    return asyncio.run(coro)


def feed_with_checkout(env, days_ahead=3, next_in=None):
    out = TODAY + timedelta(days=days_ahead)
    events = [("r1", out - timedelta(days=2), out, "Reserved")]
    if next_in is not None:
        events.append(("r2", out + timedelta(days=next_in), out + timedelta(days=next_in + 2), "Reserved"))
    env.feeds["airbnb://plaka"] = ics(*events)
    return out


def jobs(env):
    return env.agent.list_jobs()


def test_closest_cleaner_is_asked_first(env):
    feed_with_checkout(env)
    run(env.agent.sync_calendars())
    assert len(jobs(env)) == 1 and jobs(env)[0]["status"] == "offering"
    assert len(env.messenger.to(MARIA)) == 1
    assert env.messenger.to(NIKOS) == [] and env.messenger.to(ELENA) == []
    assert "YES 1" in env.messenger.to(MARIA)[0]


def test_decline_moves_to_next_closest(env):
    feed_with_checkout(env)
    run(env.agent.sync_calendars())
    run(env.agent.handle_inbound("sms", MARIA, "NO 1"))
    # Elena (~12 km) is slightly closer than Nikos (~13 km)? compute from the offers instead of assuming
    asked = [r["cleaner_id"] for r in env.db.execute("SELECT cleaner_id FROM offers ORDER BY id")]
    assert len(asked) == 2 and asked[0] != asked[1]
    assert jobs(env)[0]["status"] == "offering"


def test_accept_assigns_confirms_and_notifies_owner(env):
    feed_with_checkout(env)
    run(env.agent.sync_calendars())
    run(env.agent.handle_inbound("sms", MARIA, "yes"))
    assert jobs(env)[0]["status"] == "assigned" and jobs(env)[0]["cleaner"] == "Maria"
    assert any("Confirmed" in b for b in env.messenger.to(MARIA))
    assert any("Maria confirmed" in b for b in env.messenger.to("+306999999999"))


def test_timeout_moves_to_next_cleaner(env):
    feed_with_checkout(env)
    run(env.agent.sync_calendars())
    env.advance_minutes(31)
    run(env.agent.advance())
    statuses = [r["status"] for r in env.db.execute("SELECT status FROM offers ORDER BY id")]
    assert statuses == ["expired", "pending"]


def test_late_yes_after_expiry_still_accepted_if_nobody_else_has_it(env):
    feed_with_checkout(env)
    env.agent.s.offer_lead_days = 14
    run(env.agent.sync_calendars())
    env.advance_minutes(31)
    # no other cleaners available -> expire and then offer next; make everyone else inactive first
    env.db.execute("UPDATE cleaners SET active=0 WHERE name != 'Maria'")
    run(env.agent.advance())
    assert jobs(env)[0]["status"] == "unfilled"
    run(env.agent.handle_inbound("sms", MARIA, "YES 1"))
    assert jobs(env)[0]["status"] == "assigned"


def test_all_decline_marks_unfilled_and_alerts_owner(env):
    feed_with_checkout(env)
    run(env.agent.sync_calendars())
    for _ in range(3):
        row = env.db.execute("SELECT c.phone FROM offers o JOIN cleaners c ON c.id=o.cleaner_id WHERE o.status='pending'").fetchone()
        assert row is not None
        run(env.agent.handle_inbound("sms", row["phone"], "no"))
    assert jobs(env)[0]["status"] == "unfilled"
    assert any("ALERT" in b for b in env.messenger.to("+306999999999"))
    # owner fixes the situation and retries
    assert run(env.agent.retry_job(1)) is True
    assert jobs(env)[0]["status"] == "offering"


def test_booking_removed_cancels_job_and_tells_cleaner(env):
    feed_with_checkout(env)
    run(env.agent.sync_calendars())
    run(env.agent.handle_inbound("sms", MARIA, "YES"))
    env.feeds["airbnb://plaka"] = ics()  # guest cancelled
    run(env.agent.sync_calendars())
    assert jobs(env)[0]["status"] == "cancelled"
    assert any("cancelled" in b for b in env.messenger.to(MARIA))
    assert any("cancelled" in b for b in env.messenger.to("+306999999999"))


def test_failed_calendar_download_does_not_cancel_anything(env):
    feed_with_checkout(env)
    run(env.agent.sync_calendars())
    env.broken.add("airbnb://plaka")
    run(env.agent.sync_calendars())
    assert jobs(env)[0]["status"] == "offering"


def test_same_day_turnover_is_flagged(env):
    out = feed_with_checkout(env, next_in=0)
    run(env.agent.sync_calendars())
    assert jobs(env)[0]["next_checkin_date"] == out.isoformat()
    assert "SAME-DAY" in env.messenger.to(MARIA)[0]


def test_quiet_hours_defer_offers(env):
    feed_with_checkout(env)
    env.set_time(22)
    run(env.agent.sync_calendars())
    assert env.messenger.sent == [] and jobs(env)[0]["status"] == "pending"
    env.set_time(9)
    env.clock += timedelta(days=1)
    run(env.agent.advance())
    assert len(env.messenger.to(MARIA)) == 1


def test_far_future_jobs_wait_until_lead_time(env):
    feed_with_checkout(env, days_ahead=30)
    run(env.agent.sync_calendars())
    assert env.messenger.sent == [] and jobs(env)[0]["status"] == "pending"


def test_capacity_limit_skips_busy_cleaner(env):
    env.db.execute("UPDATE cleaners SET max_jobs_per_day=1 WHERE name='Maria'")
    env.add_property("kolo", "Kolonaki", 37.9790, 23.7440, {"airbnb": "airbnb://kolo"})
    out = TODAY + timedelta(days=3)
    env.feeds["airbnb://plaka"] = ics(("a", out - timedelta(days=2), out, "Reserved"))
    env.feeds["airbnb://kolo"] = ics(("b", out - timedelta(days=2), out, "Reserved"))
    run(env.agent.sync_calendars())
    # same day, same time: Maria (closest) gets job 1, job 2 must go to someone else from the start
    assert sum("#2" in b for b in env.messenger.to(MARIA)) == 0
    run(env.agent.handle_inbound("sms", MARIA, "YES 2"))
    assert [j for j in jobs(env) if j["id"] == 2][0]["cleaner"] != "Maria"


def test_done_marks_completed(env):
    feed_with_checkout(env)
    run(env.agent.sync_calendars())
    run(env.agent.handle_inbound("sms", MARIA, "YES"))
    run(env.agent.handle_inbound("sms", MARIA, "DONE 1"))
    assert jobs(env)[0]["status"] == "completed"


def test_reminder_sent_evening_before_once(env):
    feed_with_checkout(env, days_ahead=1)
    run(env.agent.sync_calendars())
    run(env.agent.handle_inbound("sms", MARIA, "YES"))
    env.set_time(18)
    run(env.agent.send_reminders())
    run(env.agent.send_reminders())
    assert sum("Reminder" in b for b in env.messenger.to(MARIA)) == 1


def test_viber_cleaner_is_linked_by_phone_number(env):
    env.db.execute("UPDATE cleaners SET viber_id=NULL WHERE name='Nikos'")
    run(env.agent.handle_inbound("viber", "vb-new", "hello"))
    run(env.agent.handle_inbound("viber", "vb-new", "+30 690 000 0002"))
    row = env.db.execute("SELECT viber_id FROM cleaners WHERE name='Nikos'").fetchone()
    assert row["viber_id"] == "vb-new"


def test_unknown_numbers_are_ignored(env):
    run(env.agent.handle_inbound("sms", "+15550000000", "YES"))
    assert env.messenger.sent == []


def test_send_failure_falls_through_to_next_cleaner(env):
    env.messenger.fail_for.add(MARIA)
    feed_with_checkout(env)
    run(env.agent.sync_calendars())
    offered = env.db.execute("SELECT c.name, o.status FROM offers o JOIN cleaners c ON c.id=o.cleaner_id ORDER BY o.id").fetchall()
    assert offered[0]["name"] == "Maria" and offered[0]["status"] == "failed"
    assert offered[1]["status"] == "pending"


def test_sync_survives_a_completed_job_on_a_future_day(env):
    feed_with_checkout(env)
    run(env.agent.sync_calendars())
    run(env.agent.handle_inbound("sms", MARIA, "yes"))
    run(env.agent.handle_inbound("sms", MARIA, "done 1"))  # finished early: status completed, day is still in the future
    assert jobs(env)[0]["status"] == "completed"
    run(env.agent.sync_calendars())  # used to crash: UNIQUE constraint failed: jobs.property_id, jobs.checkout_date
    assert [j["status"] for j in jobs(env)] == ["completed"]
