"""Tests for lesson schedule + substitutions sync."""

from __future__ import annotations

from unittest.mock import AsyncMock

from vulcan_notify.db import Database
from vulcan_notify.differ import diff_schedule
from vulcan_notify.models import Lesson, Student
from vulcan_notify.sync import sync_student

STUDENT = Student(
    key="S1",
    name="Solomiia",
    class_name="4E",
    school="Sz",
    diary_id=1,
    mailbox_key=None,
)


def make_lesson(
    subject: str = "Math",
    sub_teacher: str | None = None,
    sub_room: str | None = None,
    remarks: str | None = None,
    annotation: int = 0,
) -> Lesson:
    return Lesson(
        date="2026-04-15",
        time_from="2026-04-15T08:55:00+02:00",
        time_to="2026-04-15T09:40:00+02:00",
        subject=subject,
        teacher="Original T",
        room="10",
        group=None,
        annotation=annotation,
        is_extra=False,
        sub_teacher=sub_teacher,
        sub_room=sub_room,
        remarks=remarks,
    )


async def test_diff_schedule_flags_new_substitution(db: Database) -> None:
    await db.upsert_student(STUDENT)
    # First sync baseline: lesson with no substitution stored
    base = make_lesson()
    await db.upsert_lesson(STUDENT.key, base)

    # New fetch: same lesson now has a substitute teacher
    changed = make_lesson(sub_teacher="Substitute T", annotation=1)
    changes = await diff_schedule(STUDENT, [changed], db)
    assert len(changes) == 1
    assert changes[0].item_type == "substitution"
    assert changes[0].change_type == "updated"
    assert "Substitute T" in changes[0].body


async def test_diff_schedule_ignores_regular_lessons(db: Database) -> None:
    await db.upsert_student(STUDENT)
    regular = make_lesson()
    changes = await diff_schedule(STUDENT, [regular], db)
    assert changes == []


async def test_diff_schedule_flags_brand_new_substituted_lesson(db: Database) -> None:
    """Lesson appearing for the first time AND already substituted -> report."""
    await db.upsert_student(STUDENT)
    new = make_lesson(sub_teacher="Sub T", annotation=1)
    changes = await diff_schedule(STUDENT, [new], db)
    assert len(changes) == 1
    assert changes[0].change_type == "new"


async def test_sync_student_persists_and_reports(db: Database) -> None:
    # First sync: baseline, no reports
    client = AsyncMock()
    client.get_students = AsyncMock(return_value=[STUDENT])
    client.get_periods = AsyncMock(return_value=[])
    client.get_grades = AsyncMock(return_value=[])
    client.get_attendance = AsyncMock(return_value=[])
    client.get_exams = AsyncMock(return_value=[])
    client.get_homework = AsyncMock(return_value=[])
    baseline_lesson = make_lesson()
    client.get_schedule = AsyncMock(return_value=[baseline_lesson])

    result = await sync_student(client, db, STUDENT)
    assert result.is_first_sync is True
    assert result.new_substitutions == []

    # Next sync: substitution added
    changed_lesson = make_lesson(sub_teacher="Sub T", annotation=1)
    client.get_schedule = AsyncMock(return_value=[changed_lesson])
    result2 = await sync_student(client, db, STUDENT)
    assert result2.is_first_sync is False
    assert len(result2.new_substitutions) == 1
    assert result2.new_substitutions[0].change_type == "updated"
