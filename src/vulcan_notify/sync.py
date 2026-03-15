"""Sync orchestrator - fetches data from eduVulcan, stores it, detects changes."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from vulcan_notify.differ import Change, diff_attendance, diff_exams, diff_grades, diff_homework

if TYPE_CHECKING:
    from vulcan_notify.client import VulcanClient
    from vulcan_notify.db import Database
    from vulcan_notify.models import Message, Student

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Result of a single sync cycle for one student."""

    student: Student
    new_grades: list[Change] = field(default_factory=list)
    new_attendance: list[Change] = field(default_factory=list)
    new_exams: list[Change] = field(default_factory=list)
    new_homework: list[Change] = field(default_factory=list)
    unread_messages: int = 0
    is_first_sync: bool = False

    @property
    def has_changes(self) -> bool:
        return bool(self.new_grades or self.new_attendance or self.new_exams or self.new_homework)

    @property
    def all_changes(self) -> list[Change]:
        return self.new_grades + self.new_attendance + self.new_exams + self.new_homework


@dataclass
class FullSyncResult:
    """Result of a full sync cycle (all students + messages)."""

    student_results: list[SyncResult]
    new_messages: list[Message] = field(default_factory=list)
    is_first_message_sync: bool = False


async def sync_student(
    client: VulcanClient,
    db: Database,
    student: Student,
) -> SyncResult:
    """Sync a single student's data and return detected changes."""
    await db.upsert_student(student)

    # Check if this is the first sync for this student
    last_sync = await db.get_state(f"last_sync:{student.key}")
    is_first = last_sync is None

    result = SyncResult(student=student, is_first_sync=is_first)

    # ── Grades ───────────────────────────────────────────────────
    try:
        periods = await client.get_periods(student)
        if periods:
            current_period = max(periods, key=lambda p: p.number)
            grades = await client.get_grades(student, current_period)

            if not is_first:
                result.new_grades = await diff_grades(student, grades, db)

            for grade in grades:
                await db.upsert_grade(student.key, grade)
    except Exception:
        logger.exception("Failed to sync grades for %s", student.name)

    # ── Attendance ───────────────────────────────────────────────
    try:
        now = datetime.now()
        monday = now - timedelta(days=now.weekday())
        sunday = monday + timedelta(days=6)
        date_from = monday.strftime("%Y-%m-%dT00:00:00.000Z")
        date_to = sunday.strftime("%Y-%m-%dT23:59:59.999Z")

        attendance = await client.get_attendance(student, date_from, date_to)

        if not is_first:
            result.new_attendance = await diff_attendance(student, attendance, db)

        for entry in attendance:
            await db.upsert_attendance(student.key, entry)
    except Exception:
        logger.exception("Failed to sync attendance for %s", student.name)

    # ── Exams ────────────────────────────────────────────────────
    try:
        exams = await client.get_exams(student)

        if not is_first:
            result.new_exams = await diff_exams(student, exams, db)

        for exam in exams:
            await db.upsert_exam(student.key, exam)
    except Exception:
        logger.exception("Failed to sync exams for %s", student.name)

    # ── Homework ─────────────────────────────────────────────────
    try:
        homework = await client.get_homework(student)

        if not is_first:
            result.new_homework = await diff_homework(student, homework, db)

        for hw in homework:
            await db.upsert_homework(student.key, hw)
    except Exception:
        logger.exception("Failed to sync homework for %s", student.name)

    # Mark sync complete
    await db.set_state(
        f"last_sync:{student.key}",
        datetime.now().isoformat(),
    )

    return result


async def sync_messages(
    client: VulcanClient,
    db: Database,
) -> tuple[list[Message], bool]:
    """Sync messages (unified inbox). Returns (new_messages, is_first_sync)."""

    last_msg_sync = await db.get_state("last_sync:messages")
    is_first = last_msg_sync is None

    try:
        messages = await client.get_messages(page_size=50)
    except Exception:
        logger.exception("Failed to fetch messages")
        return [], is_first

    known_ids = await db.get_message_ids()
    new_messages: list[Message] = []

    for msg in messages:
        if msg.id not in known_ids and not is_first:
            new_messages.append(msg)
        await db.upsert_message(msg)

    # Fetch content for new messages
    for msg in new_messages:
        try:
            content = await client.get_message_detail(msg.api_global_key)
            if content:
                msg.content = content
                await db.update_message_content(msg.id, content)
        except Exception:
            logger.exception("Failed to fetch message detail for %d", msg.id)

    await db.set_state("last_sync:messages", datetime.now().isoformat())

    return new_messages, is_first


async def sync_all(
    client: VulcanClient,
    db: Database,
) -> FullSyncResult:
    """Sync all students and messages. Returns combined result."""
    students = await client.get_students()
    if not students:
        logger.warning("No students found in account")
        return FullSyncResult(student_results=[])

    student_results: list[SyncResult] = []
    for student in students:
        logger.info("Syncing %s (%s)...", student.name, student.class_name)
        result = await sync_student(client, db, student)
        student_results.append(result)

    # Sync messages (unified inbox, once for all students)
    logger.info("Syncing messages...")
    new_messages, is_first_msg = await sync_messages(client, db)

    return FullSyncResult(
        student_results=student_results,
        new_messages=new_messages,
        is_first_message_sync=is_first_msg,
    )
