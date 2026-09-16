"""Quiet hours must not read as staleness.

Regression cover for the night of 2026-09-15, when a five-hour scheduled pause aged
past the one-hour staleness threshold and fired 47 false Telegram alerts. The tests
pin both halves of the contract: a deliberate pause is forgiven, and a loop that
actually stopped is still caught.
"""

from datetime import datetime

import pytest

from vulcan_notify.config import settings
from vulcan_notify.freshness import ages, next_wakeup, quiet_seconds_between


@pytest.fixture(autouse=True)
def _window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production shape: 00:00-05:00 Warsaw, evaluated against UTC stamps."""
    monkeypatch.setattr(settings, "quiet_hours_start", 0)
    monkeypatch.setattr(settings, "quiet_hours_end", 5)
    monkeypatch.setattr(settings, "quiet_hours_tz", "Europe/Warsaw")


# Warsaw is UTC+2 in September, so 00:00-05:00 local is 22:00-03:00 UTC.
def _utc(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute)


def test_span_entirely_outside_the_window_counts_nothing() -> None:
    assert quiet_seconds_between(_utc(15, 10), _utc(15, 14)) == 0


def test_span_entirely_inside_the_window_counts_in_full() -> None:
    assert quiet_seconds_between(_utc(15, 23), _utc(16, 1)) == 2 * 3600


def test_span_straddling_the_window_counts_only_the_overlap() -> None:
    # 21:00 UTC -> 04:00 UTC crosses the whole 22:00-03:00 UTC quiet block.
    assert quiet_seconds_between(_utc(15, 21), _utc(16, 4)) == 5 * 3600


def test_reversed_span_is_zero_not_negative() -> None:
    assert quiet_seconds_between(_utc(15, 14), _utc(15, 10)) == 0


def test_equal_bounds_disable_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "quiet_hours_end", 0)
    assert quiet_seconds_between(_utc(15, 21), _utc(16, 4)) == 0


def test_window_wrapping_midnight(monkeypatch: pytest.MonkeyPatch) -> None:
    """23:00-05:00 local is six hours a night, not minus eighteen."""
    monkeypatch.setattr(settings, "quiet_hours_start", 23)
    monkeypatch.setattr(settings, "quiet_hours_end", 5)
    # 23:00-05:00 Warsaw is 21:00-03:00 UTC.
    assert quiet_seconds_between(_utc(15, 20), _utc(16, 4)) == 6 * 3600


def test_a_distant_stamp_does_not_walk_day_by_day() -> None:
    """Closed form, not a loop: a sentinel date must stay cheap and finite."""
    total = quiet_seconds_between(datetime(2020, 1, 1), _utc(16, 4))
    assert total > 0
    assert total < (_utc(16, 4) - datetime(2020, 1, 1)).total_seconds()


def test_overnight_pause_is_not_stale() -> None:
    """The exact false alarm: last sync 23:50 local, checked at 04:30 local."""
    stamp = _utc(15, 21, 50)  # 23:50 Warsaw, the last run before the window
    wall, effective = ages(stamp.isoformat(), _utc(16, 0, 30))  # 02:30 Warsaw

    assert wall == pytest.approx(2.67 * 3600, rel=0.01)
    # All but ten minutes of that span was the scheduled pause.
    assert effective == pytest.approx(10 * 60, abs=60)
    assert effective < settings.stale_after_seconds


def test_a_loop_that_wedged_before_quiet_hours_still_alerts() -> None:
    """The half that must keep working: real failures are not forgiven."""
    stamp = _utc(15, 18)  # 20:00 Warsaw, hours before the window opened
    _, effective = ages(stamp.isoformat(), _utc(16, 0, 30))

    assert effective > settings.stale_after_seconds


def test_a_loop_that_never_woke_up_alerts_after_the_window() -> None:
    """Forgiveness ends the moment the loop was due back at 05:00 local."""
    stamp = _utc(15, 21, 50)
    # 04:30 UTC is 06:30 Warsaw: ninety minutes after the loop should have resumed.
    _, effective = ages(stamp.isoformat(), _utc(16, 4, 30))

    assert effective > settings.stale_after_seconds


def test_wall_age_is_reported_unmodified() -> None:
    """Alerts quote wall age; a number nobody can check against a clock is worse."""
    stamp = _utc(15, 21, 50)
    wall, effective = ages(stamp.isoformat(), _utc(16, 0, 30))

    assert wall == (_utc(16, 0, 30) - stamp).total_seconds()
    assert wall > effective


def test_unusable_stamps_are_rejected_not_guessed() -> None:
    assert ages(None, _utc(16, 4)) is None
    assert ages("", _utc(16, 4)) is None
    assert ages("not-a-date", _utc(16, 4)) is None


def test_effective_age_never_goes_negative() -> None:
    _, effective = ages(_utc(15, 23).isoformat(), _utc(16, 1))
    assert effective == 0


def test_next_wakeup_reports_the_resume_time_during_the_pause() -> None:
    # 00:30 UTC is 02:30 Warsaw, inside the window.
    resume = next_wakeup(_utc(16, 0, 30))
    assert resume == datetime(2026, 9, 16, 5, 0)


def test_next_wakeup_is_none_outside_the_pause() -> None:
    # 10:00 UTC is 12:00 Warsaw.
    assert next_wakeup(_utc(16, 10)) is None


def test_unknown_timezone_falls_back_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo in QUIET_HOURS_TZ must not take the health endpoint down with it."""
    monkeypatch.setattr(settings, "quiet_hours_tz", "Mars/Olympus_Mons")
    # Falls back to reading the window in UTC, so 23:00-01:00 overlaps it by an hour.
    assert quiet_seconds_between(_utc(15, 23), _utc(16, 1)) == 3600
