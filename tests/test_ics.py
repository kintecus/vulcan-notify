"""Tests for the iCalendar serializer."""

from __future__ import annotations

from vulcan_notify.ics import _escape, _fold, _format_dt, _stable_uid, build_calendar


def test_escape_special_chars() -> None:
    assert _escape("a;b,c\nd\\e") == "a\\;b\\,c\\nd\\\\e"


def test_format_dt_to_utc() -> None:
    # CEST is UTC+2, so 08:55 local -> 06:55 UTC
    assert _format_dt("2026-04-15T08:55:00+02:00") == "20260415T065500Z"


def test_fold_long_line() -> None:
    long = "X:" + ("a" * 200)
    folded = _fold(long)
    # Continuation lines start with a space; first chunk under limit
    assert "\r\n " in folded
    # Reassembled lines cover the full payload
    reassembled = folded.replace("\r\n ", "")
    assert reassembled == long


def test_stable_uid_deterministic() -> None:
    uid_a = _stable_uid("S1", "2026-04-15", "2026-04-15T08:00:00+02:00", "Math")
    uid_b = _stable_uid("S1", "2026-04-15", "2026-04-15T08:00:00+02:00", "Math")
    uid_c = _stable_uid("S1", "2026-04-15", "2026-04-15T08:00:00+02:00", "Physics")
    assert uid_a == uid_b
    assert uid_a != uid_c
    assert uid_a.endswith("@vulcan-notify")


def test_build_calendar_basic() -> None:
    lessons = [
        {
            "date": "2026-04-15",
            "time_from": "2026-04-15T08:55:00+02:00",
            "time_to": "2026-04-15T09:40:00+02:00",
            "subject": "Math",
            "teacher": "Smith",
            "room": "10",
            "is_extra": False,
            "sub_teacher": None,
            "sub_room": None,
            "remarks": None,
            "absence_info": None,
        }
    ]
    ics = build_calendar("Solomiia", lessons, "S1")
    assert "BEGIN:VCALENDAR" in ics
    assert "END:VCALENDAR" in ics
    assert "SUMMARY:Math" in ics
    assert "LOCATION:10" in ics
    assert "DTSTART:20260415T065500Z" in ics
    assert "DTEND:20260415T074000Z" in ics
    # One VEVENT block
    assert ics.count("BEGIN:VEVENT") == 1
    # Stable UID present
    assert "UID:" in ics and "@vulcan-notify" in ics
    # CRLF line endings
    assert "\r\n" in ics


def test_build_calendar_substitution_marked() -> None:
    lessons = [
        {
            "date": "2026-04-15",
            "time_from": "2026-04-15T08:55:00+02:00",
            "time_to": "2026-04-15T09:40:00+02:00",
            "subject": "English",
            "teacher": "Smith",
            "room": "4",
            "is_extra": False,
            "sub_teacher": "Jones",
            "sub_room": "10",
            "remarks": None,
            "absence_info": None,
        }
    ]
    ics = build_calendar("Solomiia", lessons, "S1")
    assert "SUMMARY:[ZAST] English" in ics
    assert "LOCATION:10" in ics  # substituted room, not original "4"
    assert "(was: Smith)" in ics
    assert "Room changed: 4 -> 10" in ics


def test_build_calendar_empty() -> None:
    ics = build_calendar("Solomiia", [], "S1")
    assert "BEGIN:VCALENDAR" in ics
    assert "END:VCALENDAR" in ics
    assert "BEGIN:VEVENT" not in ics
