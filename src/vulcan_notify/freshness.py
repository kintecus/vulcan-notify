"""Age accounting that knows the sync loop deliberately sleeps.

`sync-loop.sh` pauses between `QUIET_HOURS_START` and `QUIET_HOURS_END`, a window far
longer than `STALE_AFTER_SECONDS`. Comparing wall-clock age against that threshold
therefore reported a perfectly healthy overnight pause as a failure: on the night of
2026-09-15 it produced 47 Telegram alerts between 02:49 and 06:59, every one of them
false.

The fix is to measure age with the *scheduled* quiet window subtracted. A loop that is
idle because it was told to be idle does not age; a loop that wedged at 20:00 still
ages past the threshold before midnight and still alerts. Only the deliberate pause is
forgiven, which is the distinction the alerting was missing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from vulcan_notify.config import settings

logger = logging.getLogger(__name__)

_HOUR = 3600.0
_DAY = 24 * _HOUR


def _local(moment: datetime) -> datetime:
    """Render a stored timestamp as local wall time in the quiet-hours zone.

    Every stamp in the database is a naive `datetime.now()` written by a container
    whose clock is UTC, so naive means UTC here. The quiet window is a human schedule
    and only makes sense in the household's own time, hence the conversion rather
    than moving the container clock and invalidating every existing row.
    """
    try:
        zone = ZoneInfo(settings.quiet_hours_tz)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "Unknown QUIET_HOURS_TZ %r, evaluating quiet hours in UTC",
            settings.quiet_hours_tz,
        )
        return moment.replace(tzinfo=None)

    aware = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment
    return aware.astimezone(zone).replace(tzinfo=None)


def _quiet_seconds_before(moment: datetime) -> float:
    """Total quiet seconds elapsed from the proleptic epoch up to `moment`.

    Closed-form rather than a day-by-day loop: `quiet_seconds_between` is called once
    per section per health request, and a stamp left at some sentinel date (tests use
    2020-01-01) would otherwise walk thousands of days to reach the same answer.
    """
    start = settings.quiet_hours_start * _HOUR
    end = settings.quiet_hours_end * _HOUR

    # Equal bounds mean no quiet window. The modulo also gives the right daily total
    # for a window that wraps midnight, e.g. 23:00-05:00 is six hours, not minus 18.
    per_day = (end - start) % _DAY
    if per_day == 0:
        return 0.0

    moment = _local(moment)
    seconds_into_day = (moment - datetime.combine(moment.date(), time.min)).total_seconds()

    if start < end:
        partial = min(max(seconds_into_day, start), end) - start
    else:
        # Wrapping window: the tail of the previous evening plus this morning's head.
        partial = min(seconds_into_day, end) + max(0.0, seconds_into_day - start)

    return moment.toordinal() * per_day + partial


_EXACT_SPAN = timedelta(days=7)


def _quiet_seconds_exact(start: datetime, end: datetime) -> float:
    """Overlap of [start, end) with each nightly window, integrated over real instants.

    The closed form runs on local wall clock, so across a DST change it is off by an
    hour. That is not harmless: on the night the clocks went back (2026-10-25) the
    00:00-05:00 window lasted six real hours, the closed form credited five, and a
    sync from 23:44 read as 76 minutes old against a 60 minute threshold. 39 false
    alerts. Spans this short are cheap to walk night by night, so do it exactly.
    """
    try:
        zone = ZoneInfo(settings.quiet_hours_tz)
    except (ZoneInfoNotFoundError, ValueError):
        zone = UTC

    # Aware datetimes sharing one tzinfo subtract as wall clock, so do the arithmetic in UTC.
    begin = (start.replace(tzinfo=UTC) if start.tzinfo is None else start).astimezone(UTC)
    finish = (end.replace(tzinfo=UTC) if end.tzinfo is None else end).astimezone(UTC)
    wraps = settings.quiet_hours_start > settings.quiet_hours_end

    total = 0.0
    day = begin.astimezone(zone).date() - timedelta(days=1)
    last_day = finish.astimezone(zone).date()
    while day <= last_day:
        opens = datetime.combine(
            day, time(hour=settings.quiet_hours_start), tzinfo=zone
        ).astimezone(UTC)
        closes_on = day + timedelta(days=1) if wraps else day
        closes = datetime.combine(
            closes_on, time(hour=settings.quiet_hours_end), tzinfo=zone
        ).astimezone(UTC)
        overlap = min(closes, finish) - max(opens, begin)
        total += max(0.0, overlap.total_seconds())
        day += timedelta(days=1)
    return total


def quiet_seconds_between(start: datetime, end: datetime) -> float:
    """Seconds in [start, end) that fall inside the configured quiet window.

    Spans up to a week are integrated over real instants so a DST change inside them
    is counted correctly. Longer spans (a sentinel stamp years back) use the wall-clock
    closed form, where an hour of DST skew is lost in the total.
    """
    if end <= start:
        return 0.0
    if settings.quiet_hours_start == settings.quiet_hours_end:
        return 0.0
    if end - start <= _EXACT_SPAN:
        return _quiet_seconds_exact(start, end)
    return _quiet_seconds_before(end) - _quiet_seconds_before(start)


def ages(raw: str | None, now: datetime) -> tuple[float, float] | None:
    """`(wall_age, effective_age)` in seconds for an ISO stamp, or None if unusable.

    Wall age is what gets reported -- "oldest 312m" in an alert should be the real
    elapsed time, not a number nobody can reconcile against a clock. Effective age is
    what the staleness threshold is compared against.
    """
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return None

    wall = (now - stamp).total_seconds()
    return wall, max(0.0, wall - quiet_seconds_between(stamp, now))


def next_wakeup(moment: datetime) -> datetime | None:
    """When the loop is due to resume, or None if `moment` is not in the quiet window.

    Reported on /api/health so a green-but-idle service explains itself instead of
    looking like a coincidence.
    """
    start = settings.quiet_hours_start
    end = settings.quiet_hours_end
    if start == end:
        return None

    # Mirrors sync-loop.sh's test exactly: hour >= START and hour < END.
    local = _local(moment)
    hour = local.hour
    in_quiet = start <= hour < end if start < end else (hour >= start or hour < end)
    if not in_quiet:
        return None

    resume = datetime.combine(local.date(), time(hour=end))
    if resume <= local:
        resume += timedelta(days=1)
    return resume
