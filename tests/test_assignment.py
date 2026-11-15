import asyncio
from datetime import timedelta

from app.agent import assignment_cost

from .conftest import TODAY, ics


def test_cost_prefers_near_but_spreads_load():
    assert assignment_cost(1.0, 0, 3) < assignment_cost(2.0, 0, 3)        # idle: nearest wins
    assert assignment_cost(1.0, 1, 3) > assignment_cost(2.0, 0, 3)        # one job held: the next-nearest idle one wins
    assert assignment_cost(12.0, 1, 3) - assignment_cost(12.0, 0, 3) > \
           assignment_cost(1.0, 1, 3) - assignment_cost(1.0, 0, 3)        # a far cleaner is penalised more per job


def test_same_time_jobs_go_to_three_different_cleaners(env):
    out = TODAY + timedelta(days=3)
    for pid in ("r2", "r3"):
        env.add_property(pid, pid, 37.9715, 23.7300, {"airbnb": f"airbnb://{pid}"})
    for pid in ("plaka", "r2", "r3"):
        env.feeds[f"airbnb://{pid}"] = ics((pid, out - timedelta(days=2), out, "Reserved"))
    asyncio.run(env.agent.sync_calendars())
    asked = [r["cleaner_id"] for r in env.db.execute("SELECT cleaner_id FROM offers WHERE status='pending'")]
    assert len(asked) == 3 and len(set(asked)) == 3


def test_preview_spreads_like_real_offers(env):
    env.set_time(22)  # quiet hours: nothing sent, the UI shows the planned cleaner instead
    out = TODAY + timedelta(days=3)
    env.add_property("r2", "r2", 37.9715, 23.7300, {"airbnb": "airbnb://r2"})
    for pid in ("plaka", "r2"):
        env.feeds[f"airbnb://{pid}"] = ics((pid, out - timedelta(days=2), out, "Reserved"))
    asyncio.run(env.agent.sync_calendars())
    planned = [j["offered_to"] for j in env.agent.list_jobs()]
    assert all(j.get("queued") for j in env.agent.list_jobs()) and len(set(planned)) == 2


def test_long_stay_gets_mid_stay_cleanings(env):
    start = TODAY + timedelta(days=1)  # Sunday 15/11: 7 nights -> cleans on day 3 and day 6, checkout on day 7
    env.feeds["airbnb://plaka"] = ics(("long", start, start + timedelta(days=7), "Reserved"))
    asyncio.run(env.agent.sync_calendars())
    js = {j["checkout_date"]: j["kind"] for j in env.agent.list_jobs()}
    assert js == {str(start + timedelta(days=3)): "stay", str(start + timedelta(days=6)): "stay",
                  str(start + timedelta(days=7)): "checkout"}


def test_same_cleaner_can_do_two_jobs_a_day_at_different_times(env):
    env.db.execute("UPDATE cleaners SET active=0 WHERE name != 'Maria'")
    out = TODAY + timedelta(days=3)
    env.add_property("r2", "r2", 37.9715, 23.7300, {"airbnb": "airbnb://r2"})
    env.db.execute("UPDATE properties SET checkout_time='13:00' WHERE id='r2'")
    for pid in ("plaka", "r2"):
        env.feeds[f"airbnb://{pid}"] = ics((pid, out - timedelta(days=2), out, "Reserved"))
    asyncio.run(env.agent.sync_calendars())
    asyncio.run(env.agent.handle_inbound("+306900000001", "YES 1"))
    asyncio.run(env.agent.handle_inbound("+306900000001", "YES 2"))
    assert [j["cleaner"] for j in env.agent.list_jobs()] == ["Maria", "Maria"]  # 11:00 and 13:00, same day
