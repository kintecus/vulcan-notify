"""Change detection - compares fetched API data against stored database rows."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vulcan_notify.db import Database
    from vulcan_notify.models import (
        AttendanceEntry,
        Exam,
        Grade,
        Homework,
        Lesson,
        Student,
    )

logger = logging.getLogger(__name__)


def _short_due(raw: str) -> str:
    """Format an ISO date(-time) string as e.g. `Mon, Apr 27`.

    Falls back to the raw string if parsing fails (Vulcan occasionally returns
    unexpected shapes).
    """
    try:
        return datetime.fromisoformat(raw).strftime("%a, %b %d")
    except (ValueError, TypeError):
        return raw


def _grade_body(grade: Grade) -> str:
    """Short category + weight, e.g. `Sprawdzian (w.3)` or just `Sprawdzian`."""
    if grade.weight and grade.weight != 1:
        return f"{grade.column_name} (w.{grade.weight})"
    return grade.column_name


@dataclass
class Change:
    """A detected change in school data."""

    change_type: str  # "new" or "updated"
    item_type: str  # "grade", "attendance", "exam", "homework"
    student_name: str
    title: str
    body: str
    priority: int = 3
    tags: list[str] | None = None
    raw: object | None = None  # original model for structured MQTT payloads
    old_value: str | None = None  # previous value (for updated grades)


async def diff_grades(
    student: Student,
    fetched: list[Grade],
    db: Database,
) -> list[Change]:
    """Compare fetched grades against stored grades.

    New column_id = new grade. Same column_id but different value = updated.
    """
    stored = await db.get_grades_for_student(student.key)
    stored_by_id = {row["column_id"]: row for row in stored}
    changes: list[Change] = []

    for grade in fetched:
        existing = stored_by_id.get(grade.column_id)

        if existing is None:
            changes.append(
                Change(
                    change_type="new",
                    item_type="grade",
                    student_name=student.name,
                    title=f"G: {grade.subject} {grade.value}",
                    body=_grade_body(grade),
                    priority=4,
                    tags=["pencil2", "school"],
                    raw=grade,
                )
            )
        elif existing["value"] != grade.value:
            old_value = str(existing["value"])
            changes.append(
                Change(
                    change_type="updated",
                    item_type="grade",
                    student_name=student.name,
                    title=f"G: {grade.subject} {old_value}→{grade.value}",
                    body=_grade_body(grade),
                    priority=4,
                    tags=["pencil2", "school"],
                    raw=grade,
                    old_value=old_value,
                )
            )

    return changes


async def diff_attendance(
    student: Student,
    fetched: list[AttendanceEntry],
    db: Database,
) -> list[Change]:
    """Compare fetched attendance against stored records.

    New (date, lesson_number) combo = new record.
    """
    stored = await db.get_attendance_for_student(student.key)
    stored_keys = {(row["date"], row["lesson_number"]) for row in stored}
    changes: list[Change] = []

    for entry in fetched:
        if (entry.date, entry.lesson_number) not in stored_keys and entry.category != 1:
            category_name = {2: "Absent", 3: "Late", 4: "Excused"}.get(
                entry.category, f"Category {entry.category}"
            )
            changes.append(
                Change(
                    change_type="new",
                    item_type="attendance",
                    student_name=student.name,
                    title=f"{category_name}: {entry.subject}",
                    body=f"{_short_due(entry.date)} • lesson {entry.lesson_number}",
                    priority=3,
                    tags=["calendar", "school"],
                    raw=entry,
                )
            )

    return changes


async def diff_exams(
    student: Student,
    fetched: list[Exam],
    db: Database,
) -> list[Change]:
    """Detect new exams (by id)."""
    stored_ids = await db.get_exam_ids_for_student(student.key)
    changes: list[Change] = []

    for exam in fetched:
        if exam.id not in stored_ids:
            exam_type = {1: "Test", 2: "Quiz"}.get(exam.type, "Exam")
            changes.append(
                Change(
                    change_type="new",
                    item_type="exam",
                    student_name=student.name,
                    title=f"{exam_type}: {exam.subject}",
                    body=_short_due(exam.date),
                    priority=3,
                    tags=["memo", "school"],
                    raw=exam,
                )
            )

    return changes


def _sub_summary(lesson: Lesson) -> str:
    parts = []
    if lesson.sub_teacher and lesson.sub_teacher != lesson.teacher:
        parts.append(f"teacher: {lesson.teacher} -> {lesson.sub_teacher}")
    if lesson.sub_room and lesson.sub_room != lesson.room:
        parts.append(f"room: {lesson.room or '?'} -> {lesson.sub_room}")
    if lesson.absence_info:
        parts.append(lesson.absence_info)
    if lesson.remarks:
        parts.append(lesson.remarks)
    return "; ".join(parts) if parts else "changed"


def _lesson_state(row: dict[str, object] | Lesson) -> tuple[object, ...]:
    """Tuple of fields we care about for change detection."""
    if isinstance(row, dict):
        return (
            row.get("sub_teacher"),
            row.get("sub_room"),
            row.get("sub_type"),
            row.get("absence_info"),
            row.get("remarks"),
            row.get("annotation"),
        )
    return (
        row.sub_teacher,
        row.sub_room,
        row.sub_type,
        row.absence_info,
        row.remarks,
        row.annotation,
    )


async def diff_schedule(
    student: Student,
    fetched: list[Lesson],
    db: Database,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[Change]:
    """Detect schedule changes: substitutions, cancellations, extra lessons.

    Scope is the `(date_from, date_to)` window; stored lessons inside that window
    that are missing from `fetched` are treated as cancellations and removed
    from the DB so downstream consumers (ICS feed, /api/schedule) stay accurate.

    - New substituted lesson appears -> "new" substitution
    - Existing lesson gains/loses substitution or remark -> "updated" substitution
    - Lesson with `is_extra=True` appears for the first time -> "new" addition
    - Stored lesson not in fetched set (within window) -> "new" cancellation, row deleted
    """
    if date_from is None:
        date_from = min((lsn.date for lsn in fetched), default=None)
    if date_to is None:
        date_to = max((lsn.date for lsn in fetched), default=None)
    stored_rows = await db.get_lessons_for_student(student.key, date_from, date_to)
    stored_by_key = {(r["date"], r["time_from"], r["subject"]): r for r in stored_rows}
    fetched_keys = {(lsn.date, lsn.time_from, lsn.subject) for lsn in fetched}
    changes: list[Change] = []

    # Substitutions + newly added lessons
    for lesson in fetched:
        key = (lesson.date, lesson.time_from, lesson.subject)
        existing = stored_by_key.get(key)

        # Brand-new "extra" lesson (dodatkowe) - notify even without substitution
        if existing is None and lesson.is_extra and not lesson.is_substituted:
            changes.append(
                Change(
                    change_type="new",
                    item_type="addition",
                    student_name=student.name,
                    title=f"+Lesson: {lesson.subject}",
                    body=(
                        f"{_short_due(lesson.date)} "
                        f"{lesson.time_from[11:16]}-{lesson.time_to[11:16]}"
                    ),
                    priority=4,
                    tags=["sparkles", "school"],
                    raw=lesson,
                )
            )
            continue

        if not lesson.is_substituted:
            continue

        new_state = _lesson_state(lesson)
        if existing is None:
            change_type = "new"
        elif _lesson_state(existing) != new_state:
            change_type = "updated"
        else:
            continue

        title = f"Sub: {lesson.subject}"
        body = (
            f"{_short_due(lesson.date)} "
            f"{lesson.time_from[11:16]}-{lesson.time_to[11:16]} • "
            f"{_sub_summary(lesson)}"
        )
        changes.append(
            Change(
                change_type=change_type,
                item_type="substitution",
                student_name=student.name,
                title=title,
                body=body,
                priority=4,
                tags=["arrows_counterclockwise", "school"],
                raw=lesson,
            )
        )

    # Cancellations: stored-but-not-fetched inside the window
    for key, row in stored_by_key.items():
        if key in fetched_keys:
            continue
        date, time_from, subject = key
        time_to = str(row.get("time_to") or "")
        when = f"{time_from[11:16]}-{time_to[11:16]}" if time_to else time_from[11:16]
        changes.append(
            Change(
                change_type="new",
                item_type="cancellation",
                student_name=student.name,
                title=f"Cancelled: {subject}",
                body=f"{_short_due(date)} {when}",
                priority=4,
                tags=["x", "school"],
            )
        )
        await db.delete_lesson(student.key, date, time_from, subject)

    return changes


async def diff_homework(
    student: Student,
    fetched: list[Homework],
    db: Database,
) -> list[Change]:
    """Detect new homework (by id)."""
    stored_ids = await db.get_homework_ids_for_student(student.key)
    changes: list[Change] = []

    for hw in fetched:
        if hw.id not in stored_ids:
            changes.append(
                Change(
                    change_type="new",
                    item_type="homework",
                    student_name=student.name,
                    title=f"HW: {hw.subject}",
                    body=f"Due: {_short_due(hw.date)}",
                    priority=2,
                    tags=["books", "school"],
                    raw=hw,
                )
            )

    return changes
