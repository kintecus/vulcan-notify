"""Minimal RFC 5545 iCalendar serializer for lesson schedules.

No external dependencies - we control the input, so we can be strict about
escaping and datetime formatting without pulling in a full ics library.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime

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
    """
    dt = datetime.fromisoformat(iso_with_tz).astimezone(UTC)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _stable_uid(student_key: str, date: str, time_from: str, subject: str) -> str:
    """Derive a stable VEVENT UID so updates replace prior instances."""
    payload = f"{student_key}|{date}|{time_from}|{subject}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"{digest}@vulcan-notify"


def build_calendar(
    student_name: str, lessons: Iterable[dict[str, object]], student_key: str
) -> str:
    """Build an RFC 5545 iCalendar document from schedule rows.

    `lessons` rows are the dicts returned by Database.get_lessons_for_student.
    """
    now_utc = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//vulcan-notify//school schedule//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        _fold(f"X-WR-CALNAME:School - {_escape(student_name)}"),
    ]

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

        uid = _stable_uid(student_key, date, time_from, subject)

        lines.extend(
            [
                "BEGIN:VEVENT",
                _fold(f"UID:{uid}"),
                f"DTSTAMP:{now_utc}",
                f"DTSTART:{_format_dt(time_from)}",
                f"DTEND:{_format_dt(time_to)}",
                _fold(f"SUMMARY:{_escape(summary)}"),
                _fold(f"DESCRIPTION:{_escape(description)}"),
            ]
        )
        if effective_room:
            lines.append(_fold(f"LOCATION:{_escape(effective_room)}"))
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return _CRLF.join(lines) + _CRLF
