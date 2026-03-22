"""Sync orchestrator - fetches data from eduVulcan, stores it, detects changes."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from vulcan_notify.config import settings
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
        for period in periods:
            grades = await client.get_grades(student, period)

            if not is_first:
                result.new_grades.extend(await diff_grades(student, grades, db))

            for grade in grades:
                await db.upsert_grade(student.key, grade)
    except Exception:
        logger.exception("Failed to sync grades for %s", student.name)

    # ── Attendance ───────────────────────────────────────────────
    try:
        now = datetime.now()
        date_from = (now - timedelta(days=settings.sync_attendance_days)).strftime(
            "%Y-%m-%dT00:00:00.000Z"
        )
        date_to = now.strftime("%Y-%m-%dT23:59:59.999Z")

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
        stored_exam_ids = await db.get_exam_ids_for_student(student.key)

        if not is_first:
            result.new_exams = await diff_exams(student, exams, db)

        for exam in exams:
            await db.upsert_exam(student.key, exam)

        # Fetch detail for new exams
        for exam in exams:
            if exam.id not in stored_exam_ids:
                try:
                    detail = await client.get_exam_detail(exam.id)
                    if detail and isinstance(detail, dict):
                        description = detail.get("opis") or detail.get("tresc")
                        teacher = detail.get("nauczyciel")
                        if description:
                            await db.update_exam_description(
                                exam.id, str(description), str(teacher) if teacher else None
                            )
                except Exception:
                    logger.debug("Failed to fetch exam detail for %d", exam.id)

        # Mark exams no longer returned by API as soft-deleted
        if not is_first:
            deleted = await db.mark_missing(student.key, "exams", {e.id for e in exams})
            if deleted:
                logger.info("Soft-deleted %d exams for %s", deleted, student.name)
    except Exception:
        logger.exception("Failed to sync exams for %s", student.name)

    # ── Homework ─────────────────────────────────────────────────
    try:
        homework = await client.get_homework(student)
        stored_hw_ids = await db.get_homework_ids_for_student(student.key)

        if not is_first:
            result.new_homework = await diff_homework(student, homework, db)

        for hw in homework:
            await db.upsert_homework(student.key, hw)

        # Fetch detail for new homework
        for hw in homework:
            if hw.id not in stored_hw_ids:
                try:
                    detail = await client.get_homework_detail(hw.id)
                    if detail and isinstance(detail, dict):
                        content = detail.get("tresc") or detail.get("opis")
                        teacher = detail.get("nauczyciel")
                        if content:
                            await db.update_homework_content(
                                hw.id, str(content), str(teacher) if teacher else None
                            )
                except Exception:
                    logger.debug("Failed to fetch homework detail for %d", hw.id)

        # Mark homework no longer returned by API as soft-deleted
        if not is_first:
            deleted = await db.mark_missing(student.key, "homework", {h.id for h in homework})
            if deleted:
                logger.info("Soft-deleted %d homework for %s", deleted, student.name)
    except Exception:
        logger.exception("Failed to sync homework for %s", student.name)

    # Commit all entity upserts in one transaction
    await db.commit()

    # Mark sync complete
    await db.set_state(
        f"last_sync:{student.key}",
        datetime.now().isoformat(),
    )
    await db.commit()

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

    await db.commit()

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
    await db.commit()

    return new_messages, is_first


async def sync_all(
    client: VulcanClient,
    db: Database,
) -> FullSyncResult:
    """Sync all students and messages. Returns combined result."""
    run_id = await db.create_sync_run()
    errors = 0
    items = 0

    try:
        students = await client.get_students()
        if not students:
            logger.warning("No students found in account")
            await db.complete_sync_run(run_id, "completed", 0, 0, 0)
            return FullSyncResult(student_results=[])

        student_results: list[SyncResult] = []
        for student in students:
            logger.info("Syncing %s (%s)...", student.name, student.class_name)
            result = await sync_student(client, db, student)
            student_results.append(result)
            items += len(result.all_changes)

        # Sync messages (unified inbox, once for all students)
        logger.info("Syncing messages...")
        new_messages, is_first_msg = await sync_messages(client, db)
        items += len(new_messages)

        await db.complete_sync_run(
            run_id, "completed", len(students), items, errors
        )

        return FullSyncResult(
            student_results=student_results,
            new_messages=new_messages,
            is_first_message_sync=is_first_msg,
        )
    except Exception as exc:
        await db.complete_sync_run(
            run_id, "failed", 0, items, errors + 1, str(exc)
        )
        raise
