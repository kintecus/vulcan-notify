"""macOS Calendar integration via AppleScript."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from vulcan_notify.config import settings

if TYPE_CHECKING:
    from vulcan_notify.db import Database

logger = logging.getLogger(__name__)

_EXAM_TYPE_NAMES = {
    1: "Sprawdzian",
    2: "Kartkowka",
}


@dataclass
class CalendarSyncResult:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    errors: int = 0
    skipped_students: list[str] = field(default_factory=list)


def _parse_date(iso_date: str) -> str:
    """Parse ISO 8601 date string to YYYY-MM-DD for AppleScript."""
    try:
        dt = datetime.fromisoformat(iso_date)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return iso_date[:10]


def _exam_title(subject: str, exam_type: int) -> str:
    type_name = _EXAM_TYPE_NAMES.get(exam_type, "Sprawdzian/kartkowka")
    return f"{type_name} - {subject}"


def _homework_title(subject: str) -> str:
    return f"Zadanie domowe - {subject}"


def _event_body(description: str | None, teacher: str | None) -> str:
    parts = []
    if description:
        parts.append(description)
    if teacher:
        parts.append(f"Nauczyciel: {teacher}")
    return "\n".join(parts)


def _escape_applescript(text: str) -> str:
    """Escape text for use in AppleScript string literals."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


async def _run_applescript(script: str) -> str:
    """Run an AppleScript and return stdout."""
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        error_msg = stderr.decode().strip()
        raise RuntimeError(f"AppleScript failed: {error_msg}")
    return stdout.decode().strip()


async def _create_event(
    calendar_name: str,
    title: str,
    date: str,
    description: str,
    reminder_hours: int,
) -> str:
    """Create an all-day calendar event. Returns the event UID."""
    title_esc = _escape_applescript(title)
    desc_esc = _escape_applescript(description)
    cal_esc = _escape_applescript(calendar_name)
    reminder_minutes = reminder_hours * 60

    script = f'''
tell application "Calendar"
    tell calendar "{cal_esc}"
        set eventDate to current date
        set year of eventDate to {date[:4]}
        set month of eventDate to {date[5:7]}
        set day of eventDate to {date[8:10]}
        set hours of eventDate to 0
        set minutes of eventDate to 0
        set seconds of eventDate to 0
        set eventProps to {{summary:"{title_esc}", ¬
            start date:eventDate, end date:eventDate, ¬
            allday event:true, description:"{desc_esc}"}}
        set newEvent to make new event with properties eventProps
        tell newEvent
            set alarmProps to {{trigger interval:-{reminder_minutes}}}
            make new sound alarm at end of sound alarms with properties alarmProps
        end tell
        return uid of newEvent
    end tell
end tell
'''
    return await _run_applescript(script)


async def _update_event(
    calendar_name: str,
    uid: str,
    title: str,
    date: str,
    description: str,
) -> None:
    """Update an existing calendar event by UID."""
    title_esc = _escape_applescript(title)
    desc_esc = _escape_applescript(description)
    cal_esc = _escape_applescript(calendar_name)
    uid_esc = _escape_applescript(uid)

    script = f'''
tell application "Calendar"
    tell calendar "{cal_esc}"
        set eventDate to current date
        set year of eventDate to {date[:4]}
        set month of eventDate to {date[5:7]}
        set day of eventDate to {date[8:10]}
        set hours of eventDate to 0
        set minutes of eventDate to 0
        set seconds of eventDate to 0
        set targetEvent to first event whose uid is "{uid_esc}"
        set summary of targetEvent to "{title_esc}"
        set start date of targetEvent to eventDate
        set end date of targetEvent to eventDate
        set description of targetEvent to "{desc_esc}"
    end tell
end tell
'''
    await _run_applescript(script)


async def _delete_event(calendar_name: str, uid: str) -> None:
    """Delete a calendar event by UID."""
    cal_esc = _escape_applescript(calendar_name)
    uid_esc = _escape_applescript(uid)

    script = f'''
tell application "Calendar"
    tell calendar "{cal_esc}"
        delete (first event whose uid is "{uid_esc}")
    end tell
end tell
'''
    await _run_applescript(script)


async def sync_to_calendar(db: Database) -> CalendarSyncResult:
    """Push all active exams/homework to macOS Calendar.

    Creates events for items without calendar_uid.
    Updates events for items with calendar_uid.
    Deletes events for soft-deleted items that still have calendar_uid.
    """
    calendar_map = settings.calendar_map
    if not calendar_map:
        return CalendarSyncResult()

    reminder_hours = settings.calendar_reminder_hours
    result = CalendarSyncResult()

    # Get all students to resolve student_key -> name
    students_cursor = await db.db.execute("SELECT key, name FROM students")
    students = {row[0]: row[1] for row in await students_cursor.fetchall()}

    for student_key, student_name in students.items():
        calendar_name = calendar_map.get(student_name)
        if not calendar_name:
            result.skipped_students.append(student_name)
            continue

        # Handle soft-deleted items with calendar events
        deleted = await db.get_deleted_items_with_calendar_uid(student_key)
        for table in ("exams", "homework"):
            for item in deleted[table]:
                try:
                    await _delete_event(calendar_name, str(item["calendar_uid"]))
                    await db.clear_calendar_uid(table, int(str(item["id"])))
                    result.deleted += 1
                except Exception:
                    logger.debug(
                        "Failed to delete calendar event for %s %s, clearing UID",
                        table, item["id"],
                    )
                    await db.clear_calendar_uid(table, int(str(item["id"])))
                    result.errors += 1

        # Sync active items
        items = await db.get_items_for_calendar(student_key)

        for exam in items["exams"]:
            title = _exam_title(str(exam["subject"]), int(str(exam["type"] or 0)))
            date = _parse_date(str(exam["date"]))
            body = _event_body(
                str(exam["description"]) if exam["description"] else None,
                str(exam["teacher"]) if exam["teacher"] else None,
            )

            if exam["calendar_uid"]:
                try:
                    await _update_event(
                        calendar_name, str(exam["calendar_uid"]),
                        title, date, body,
                    )
                    result.updated += 1
                except Exception:
                    logger.debug(
                        "Failed to update exam %s, clearing stale UID", exam["id"]
                    )
                    await db.clear_calendar_uid("exams", int(str(exam["id"])))
                    result.errors += 1
            else:
                try:
                    uid = await _create_event(
                        calendar_name, title, date, body, reminder_hours,
                    )
                    await db.set_calendar_uid("exams", int(str(exam["id"])), uid)
                    result.created += 1
                except Exception:
                    logger.exception("Failed to create exam event for %s", exam["id"])
                    result.errors += 1

        for hw in items["homework"]:
            title = _homework_title(str(hw["subject"]))
            date = _parse_date(str(hw["date"]))
            body = _event_body(
                str(hw["content"]) if hw["content"] else None,
                str(hw["teacher"]) if hw["teacher"] else None,
            )

            if hw["calendar_uid"]:
                try:
                    await _update_event(
                        calendar_name, str(hw["calendar_uid"]),
                        title, date, body,
                    )
                    result.updated += 1
                except Exception:
                    logger.debug(
                        "Failed to update homework %s, clearing stale UID", hw["id"]
                    )
                    await db.clear_calendar_uid("homework", int(str(hw["id"])))
                    result.errors += 1
            else:
                try:
                    uid = await _create_event(
                        calendar_name, title, date, body, reminder_hours,
                    )
                    await db.set_calendar_uid("homework", int(str(hw["id"])), uid)
                    result.created += 1
                except Exception:
                    logger.exception(
                        "Failed to create homework event for %s", hw["id"]
                    )
                    result.errors += 1

    await db.commit()
    return result
