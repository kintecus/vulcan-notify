"""Minimal RFC 5545 iCalendar serializer for lesson schedules.

No external dependencies - we control the input, so we can be strict about
escaping and datetime formatting without pulling in a full ics library.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

_CRLF = "\r\n"


def _escape(value: str) -> str:
    """Escape TEXT-typed values per RFC 5545 §3.3.11."""
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Fold lines longer than 75 octets per RFC 5545 §3.1."""
    if len(line.encode("utf-8")) <= 75:
        return line
    # Fold on character boundaries, conservative 73-byte chunks
    out: list[str] = []
    buf = ""
    for ch in line:
        if len((buf + ch).encode("utf-8")) > 73:
            out.append(buf)
            buf = " " + ch  # continuation lines start with a space
        else:
            buf += ch
    out.append(buf)
    return _CRLF.join(out)


def _format_dt(iso_with_tz: str) -> str:
    """Convert a local ISO datetime (with offset) to a UTC DATE-TIME string.

    Input examples: '2026-04-15T08:55:00+02:00'
    Output: '20260415T065500Z'

    Raises ValueError on unparseable input; build_calendar skips those rows rather
    than letting one bad lesson 500 the whole feed.
    """
    dt = datetime.fromisoformat(iso_with_tz).astimezone(UTC)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _parse_stamp(raw: object) -> datetime | None:
    """Best-effort parse of a DB timestamp into an aware UTC datetime."""
    if not raw:
        return None
    text = str(raw).replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _stable_uid(student_key: str, date: str, time_from: str, subject: str) -> str:
    """Derive a stable VEVENT UID so updates replace prior instances."""
    payload = f"{student_key}|{date}|{time_from}|{subject}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"{digest}@vulcan-notify"


def _stale_event(student_key: str, stale_since: datetime | None, now: datetime) -> list[str]:
    """An all-day event announcing that the feed has stopped updating.

    The calendar is the one surface checked daily without thinking about it, so a
    frozen feed has to say so there. Without this it keeps serving last week's
    timetable and looks authoritative. The UID is stable per student, so the event
    replaces itself rather than piling up, and vanishes once a sync succeeds.

    `stale_since` is None when no successful fetch has ever been recorded -- say so
    rather than inventing a timestamp.
    """
    digest = hashlib.sha1(f"stale|{student_key}".encode()).hexdigest()[:16]
    today = now.strftime("%Y%m%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y%m%d")

    if stale_since is None:
        summary = "⚠️ School sync has not completed"
        detail = "vulcan-notify has no record of a successful schedule fetch."
    else:
        since = stale_since.strftime("%Y-%m-%d %H:%M")
        summary = f"⚠️ School sync stale since {since}"
        detail = (
            f"vulcan-notify has not successfully fetched the schedule since {since}."
        )

    # DTSTAMP is pinned to the day, not the request: a per-request stamp would make
    # every poll look like the warning had changed.
    return [
        "BEGIN:VEVENT",
        _fold(f"UID:stale-{digest}@vulcan-notify"),
        f"DTSTAMP:{today}T000000Z",
        f"DTSTART;VALUE=DATE:{today}",
        f"DTEND;VALUE=DATE:{tomorrow}",
        _fold(f"SUMMARY:{_escape(summary)}"),
        _fold(_escape_desc(f"{detail} Lessons shown may be out of date.")),
        "END:VEVENT",
    ]


def _escape_desc(text: str) -> str:
    return f"DESCRIPTION:{_escape(text)}"


def build_calendar(
    student_name: str,
    lessons: Iterable[dict[str, object]],
    student_key: str,
    *,
    stale: bool = False,
    stale_since: datetime | None = None,
) -> str:
    """Build an RFC 5545 iCalendar document from schedule rows.

    `lessons` rows are the dicts returned by Database.get_lessons_for_student.
    `student_key` is the UID salt fallback for rows that don't carry their own
    (rows may span several keys when a school year rolls over).
    `stale` prepends a warning event -- see _stale_event. `stale_since` dates it, and
    may be None when nothing has ever succeeded.
    """
    now = datetime.now(UTC)
    now_utc = now.strftime("%Y%m%dT%H%M%SZ")
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//vulcan-notify//school schedule//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        _fold(f"X-WR-CALNAME:School - {_escape(student_name)}"),
        # Tell subscribers how often to come back. Without these, Apple Calendar's
        # "Auto" is opaque and can sit for hours on a changed timetable.
        "X-PUBLISHED-TTL:PT1H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
    ]

    if stale:
        lines.extend(_stale_event(student_key, stale_since, now))

    for lesson in lessons:
        date = str(lesson["date"])
        time_from = str(lesson["time_from"])
        time_to = str(lesson["time_to"])
        subject = str(lesson["subject"])
        teacher = str(lesson.get("teacher") or "")
        room = str(lesson.get("room") or "")
        sub_teacher = lesson.get("sub_teacher")
        sub_room = lesson.get("sub_room")
        remarks = lesson.get("remarks")
        absence = lesson.get("absence_info")
        is_extra = bool(lesson.get("is_extra"))

        effective_teacher = str(sub_teacher) if sub_teacher else teacher
        effective_room = str(sub_room) if sub_room else room
        is_sub = bool(sub_teacher or sub_room or remarks or absence)

        summary = subject
        if is_sub:
            summary = f"[ZAST] {subject}"
        elif is_extra:
            summary = f"[EXTRA] {subject}"

        desc_lines = [f"Teacher: {effective_teacher}"]
        if sub_teacher and sub_teacher != teacher:
            desc_lines.append(f"(was: {teacher})")
        if sub_room and sub_room != room:
            desc_lines.append(f"Room changed: {room or '?'} -> {sub_room}")
        if absence:
            desc_lines.append(f"Note: {absence}")
        if remarks:
            desc_lines.append(f"Remarks: {remarks}")
        description = "\n".join(desc_lines)

        uid = _stable_uid(str(lesson.get("student_key") or student_key), date, time_from, subject)

        try:
            dtstart = _format_dt(time_from)
            dtend = _format_dt(time_to)
        except ValueError:
            # One malformed row used to 500 the entire feed, taking every other
            # lesson down with it. Drop the bad lesson, serve the rest.
            logger.warning(
                "Skipping lesson with unparseable time (%s %s '%s')", date, time_from, subject
            )
            continue

        # DTSTAMP is when this event last actually changed, not when it was served.
        # Regenerating it per request made every poll look like every event had been
        # modified, which churns subscribers for no reason.
        changed = _parse_stamp(lesson.get("last_seen")) or _parse_stamp(lesson.get("first_seen"))
        stamp = changed.strftime("%Y%m%dT%H%M%SZ") if changed else now_utc

        lines.extend(
            [
                "BEGIN:VEVENT",
                _fold(f"UID:{uid}"),
                f"DTSTAMP:{stamp}",
                f"LAST-MODIFIED:{stamp}",
                "SEQUENCE:0",
                f"DTSTART:{dtstart}",
                f"DTEND:{dtend}",
                _fold(f"SUMMARY:{_escape(summary)}"),
                _fold(f"DESCRIPTION:{_escape(description)}"),
            ]
        )
        if effective_room:
            lines.append(_fold(f"LOCATION:{_escape(effective_room)}"))
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return _CRLF.join(lines) + _CRLF
