from datetime import date

from app.geo import haversine_km, normalize_phone
from app.ical import parse_ics
from app.replies import parse_reply
from app.templates import fmt_date, t

from .conftest import ics


def test_haversine_athens_to_piraeus():
    km = haversine_km(37.9755, 23.7348, 37.9420, 23.6470)
    assert 7.5 < km < 9.0


def test_normalize_phone():
    assert normalize_phone("+30 691 234 5678") == "+306912345678"
    assert normalize_phone("0030 691 234 5678") == "+306912345678"
    assert normalize_phone("6912345678") == "+306912345678"
    assert normalize_phone("") == ""


def test_parse_reply_variants():
    assert parse_reply("YES 12") == ("yes", 12)
    assert parse_reply("yes") == ("yes", None)
    assert parse_reply("Ναι #7") == ("yes", 7)
    assert parse_reply("ΝΑΙ") == ("yes", None)
    assert parse_reply("no 3") == ("no", 3)
    assert parse_reply("Όχι") == ("no", None)
    assert parse_reply("done 4") == ("done", 4)
    assert parse_reply("ΤΕΛΟΣ 4") == ("done", 4)
    assert parse_reply("what?") == ("unknown", None)


def test_airbnb_blocks_ignored_but_booking_events_kept():
    feed = ics(
        ("a", date(2026, 11, 20), date(2026, 11, 23), "Reserved"),
        ("b", date(2026, 11, 25), date(2026, 11, 28), "Airbnb (Not available)"),
    )
    assert [r.uid for r in parse_ics(feed, "airbnb")] == ["a"]
    booking_feed = ics(("c", date(2026, 12, 1), date(2026, 12, 3), "CLOSED - Not available"))
    assert [r.uid for r in parse_ics(booking_feed, "booking")] == ["c"]


def test_templates_render_in_both_languages():
    for lang in ("en", "el"):
        txt = t(lang, "offer", name="X", id=1, property="P", address="A", date=fmt_date(date(2026, 11, 21), lang),
                time="11:00", turnover="", km="1.2")
        assert "#1" in txt and "1.2" in txt
