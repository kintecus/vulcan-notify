"""Change detection - compares fetched API data against stored database rows."""

from __future__ import annotations

import logging
from dataclasses import dataclass
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
                    title=f"New grade: {grade.value} in {grade.subject}",
                    body=(
                        f"Subject: {grade.subject}\n"
                        f"Grade: {grade.value}\n"
                        f"Category: {grade.column_name}\n"
                        f"Teacher: {grade.teacher}"
                    ),
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
                    title=f"Grade changed: {old_value} -> {grade.value} in {grade.subject}",
                    body=(
                        f"Subject: {grade.subject}\n"
                        f"Old: {old_value} -> New: {grade.value}\n"
                        f"Category: {grade.column_name}\n"
                        f"Teacher: {grade.teacher}"
                    ),
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
                    title=f"Attendance: {category_name}",
                    body=(
                        f"Date: {entry.date}\n"
                        f"Lesson {entry.lesson_number}: {entry.subject}\n"
                        f"Teacher: {entry.teacher}"
                    ),
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
                    title=f"Upcoming {exam_type}: {exam.subject}",
                    body=f"Date: {exam.date}\nSubject: {exam.subject}",
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
) -> list[Change]:
    """Detect new or changed substitutions in the upcoming schedule.

    We only report lessons that are substituted (have zmiany / remarks / annotation).
    Unchanged regular lessons are stored silently for baselining.
    """
    date_from = min((lsn.date for lsn in fetched), default=None)
    date_to = max((lsn.date for lsn in fetched), default=None)
    stored_rows = await db.get_lessons_for_student(student.key, date_from, date_to)
    stored_by_key = {(r["date"], r["time_from"], r["subject"]): r for r in stored_rows}
    changes: list[Change] = []

    for lesson in fetched:
        key = (lesson.date, lesson.time_from, lesson.subject)
        existing = stored_by_key.get(key)

        if not lesson.is_substituted:
            continue

        new_state = _lesson_state(lesson)
        if existing is None:
            change_type = "new"
        elif _lesson_state(existing) != new_state:
            change_type = "updated"
        else:
            continue

        title = f"Substitution: {lesson.subject} ({lesson.date})"
        body_lines = [
            f"Date: {lesson.date}",
            f"Time: {lesson.time_from[11:16]}-{lesson.time_to[11:16]}",
            f"Subject: {lesson.subject}",
            f"Change: {_sub_summary(lesson)}",
        ]
        changes.append(
            Change(
                change_type=change_type,
                item_type="substitution",
                student_name=student.name,
                title=title,
                body="\n".join(body_lines),
                priority=4,
                tags=["arrows_counterclockwise", "school"],
                raw=lesson,
            )
        )

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
                    title=f"Homework: {hw.subject}",
                    body=f"Due: {hw.date}\nSubject: {hw.subject}",
                    priority=2,
                    tags=["books", "school"],
                    raw=hw,
                )
            )

    return changes
