"""Sync orchestrator - fetches data from eduVulcan, stores it, detects changes."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from vulcan_notify.config import settings
from vulcan_notify.differ import (
    Change,
    diff_attendance,
    diff_exams,
    diff_grades,
    diff_homework,
    diff_schedule,
)

if TYPE_CHECKING:
    from vulcan_notify.client import VulcanClient
    from vulcan_notify.db import Database
    from vulcan_notify.models import Grade, Message, Student

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Result of a single sync cycle for one student."""

    student: Student
    new_grades: list[Change] = field(default_factory=list)
    new_attendance: list[Change] = field(default_factory=list)
    new_exams: list[Change] = field(default_factory=list)
    new_homework: list[Change] = field(default_factory=list)
    new_substitutions: list[Change] = field(default_factory=list)
    unread_messages: int = 0
    is_first_sync: bool = False
    failed_sections: dict[str, str] = field(default_factory=dict)

    @property
    def has_failures(self) -> bool:
        return bool(self.failed_sections)

    @property
    def has_changes(self) -> bool:
        return bool(
            self.new_grades
            or self.new_attendance
            or self.new_exams
            or self.new_homework
            or self.new_substitutions
        )

    @property
    def all_changes(self) -> list[Change]:
        return (
            self.new_grades
            + self.new_attendance
            + self.new_exams
            + self.new_homework
            + self.new_substitutions
        )


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
    run_id: int | None = None,
) -> SyncResult:
    """Sync a single student's data and return detected changes.

    Each section records its own outcome against run_id. A section that raises is
    logged, recorded as failed, and does NOT stamp a freshness timestamp -- that is
    what makes a partial outage visible instead of looking like a quiet day.
    """
    await db.upsert_student(student)

    # Check if this is the first sync for this student
    last_sync = await db.get_state(f"last_sync:{student.key}")
    is_first = last_sync is None

    result = SyncResult(student=student, is_first_sync=is_first)

    async def section_ok(section: str, item_count: int) -> None:
        if run_id is not None:
            await db.record_section(
                run_id, section, "ok", student_key=student.key, item_count=item_count
            )

    async def section_failed(section: str, exc: Exception) -> None:
        detail = f"{type(exc).__name__}: {exc}"[:500]
        result.failed_sections[section] = detail
        logger.exception("Failed to sync %s for %s", section, student.name)
        if run_id is not None:
            await db.record_section(
                run_id, section, "failed", student_key=student.key, error_detail=detail
            )

    # ── Grades ───────────────────────────────────────────────────
    try:
        periods = await client.get_periods(student)
        for period in periods:
            await db.upsert_classification_period(
                student.key, period.id, period.number, period.date_from, period.date_to
            )
        # Dedupe by (period_id, column_id). When a column has both an original
        # and an improvement grade (Vulcan stores them side by side and links
        # them via idOcenaPoprawiona on the original), prefer the row WITHOUT
        # superseded_by_grade_id set — that's the improved grade, which is
        # what Vulcan's parent UI shows as the current grade.
        all_grades: dict[tuple[int, int], Grade] = {}
        for period in periods:
            grades, summaries = await client.get_grades_and_summaries(student, period)
            for grade in grades:
                key = (period.id, grade.column_id)
                existing = all_grades.get(key)
                if existing is None:
                    all_grades[key] = grade
                elif existing.superseded_by_grade_id is not None and grade.superseded_by_grade_id is None:
                    # Replace the original with the improvement
                    all_grades[key] = grade
            for summary in summaries:
                await db.upsert_subject_summary(student.key, summary)

        deduplicated = list(all_grades.values())
        if not is_first:
            result.new_grades.extend(await diff_grades(student, deduplicated, db))

        for grade in deduplicated:
            await db.upsert_grade(student.key, grade)
        await section_ok("grades", len(deduplicated))
    except Exception as exc:
        await section_failed("grades", exc)

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
        await section_ok("attendance", len(attendance))
    except Exception as exc:
        await section_failed("attendance", exc)

    # ── Exams ────────────────────────────────────────────────────
    try:
        exams = await client.get_exams(student)
        stored_exam_ids = await db.get_exam_ids_for_student(student.key)

        if not is_first:
            result.new_exams = await diff_exams(student, exams, db)

        for exam in exams:
            await db.upsert_exam(student.key, exam)

        # Fetch detail for new exams or exams missing description
        missing_detail = await db.get_exams_missing_detail(student.key)
        for exam in exams:
            if exam.id not in stored_exam_ids or exam.id in missing_detail:
                try:
                    detail = await client.get_exam_detail(student, exam.id)
                    if detail:
                        description = detail.get("opis", "")
                        teacher = detail.get("nauczycielImieNazwisko", "")
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
        await section_ok("exams", len(exams))
    except Exception as exc:
        await section_failed("exams", exc)

    # ── Homework ─────────────────────────────────────────────────
    try:
        homework = await client.get_homework(student)
        stored_hw_ids = await db.get_homework_ids_for_student(student.key)

        if not is_first:
            result.new_homework = await diff_homework(student, homework, db)

        for hw in homework:
            await db.upsert_homework(student.key, hw)

        # Fetch detail for new homework or homework missing content
        missing_detail = await db.get_homework_missing_detail(student.key)
        for hw in homework:
            if hw.id not in stored_hw_ids or hw.id in missing_detail:
                try:
                    detail = await client.get_homework_detail(student, hw.id)
                    if detail:
                        content = detail.get("opis", "")
                        teacher = detail.get("nauczycielImieNazwisko", "")
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
        await section_ok("homework", len(homework))
    except Exception as exc:
        await section_failed("homework", exc)

    # ── Schedule / Substitutions ─────────────────────────────────
    try:
        now = datetime.now()
        # Previous week + next two weeks, expressed as UTC ISO for the API.
        date_from_api = (now - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00.000Z")
        date_to_api = (now + timedelta(days=14)).strftime("%Y-%m-%dT23:59:59.999Z")

        lessons = await client.get_schedule(student, date_from_api, date_to_api)

        # Anchor the diff window to the fetched lessons' actual local-date range
        # so that timezone drift in the UTC API window doesn't park a lesson
        # outside the DB lookup and resurrect it as "new" on every sync.
        if lessons:
            date_from_local = min(lesson.date for lesson in lessons)
            date_to_local = max(lesson.date for lesson in lessons)
        else:
            date_from_local = (now - timedelta(days=7)).strftime("%Y-%m-%d")
            date_to_local = (now + timedelta(days=14)).strftime("%Y-%m-%d")

        if not is_first:
            result.new_substitutions = await diff_schedule(
                student, lessons, db, date_from_local, date_to_local
            )

        for lesson in lessons:
            await db.upsert_lesson(student.key, lesson)
        await section_ok("schedule", len(lessons))
    except Exception as exc:
        await section_failed("schedule", exc)

    # Commit all entity upserts in one transaction
    await db.commit()

    # Mark sync attempted. This is the "have we ever seen this student" flag that
    # drives is_first_sync -- deliberately NOT a freshness signal. Freshness lives in
    # last_success:<student>:<section>, which only advances on a confirmed fetch.
    await db.set_state(
        f"last_sync:{student.key}",
        datetime.now().isoformat(),
    )
    await db.commit()

    return result


async def sync_messages(
    client: VulcanClient,
    db: Database,
    run_id: int | None = None,
) -> tuple[list[Message], bool, str | None]:
    """Sync messages (unified inbox).

    Returns (new_messages, is_first_sync, failure_detail). failure_detail is None on
    success; when set, the caller marks the run degraded.
    """

    last_msg_sync = await db.get_state("last_sync:messages")
    is_first = last_msg_sync is None

    try:
        messages = await client.get_messages(page_size=50)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"[:500]
        logger.exception("Failed to fetch messages")
        if run_id is not None:
            await db.record_section(run_id, "messages", "failed", error_detail=detail)
        return [], is_first, detail

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

    # Backfill historical messages missing content, bounded per cycle to
    # avoid hammering the upstream API. Safe to re-run; idempotent.
    backfill = await db.get_messages_missing_content(limit=settings.sync_message_backfill_batch)
    for msg_id, api_key in backfill:
        try:
            content = await client.get_message_detail(api_key)
            if content:
                await db.update_message_content(msg_id, content)
        except Exception:
            logger.exception("Failed to backfill message detail for %d", msg_id)
    if backfill:
        logger.info("Backfilled content for %d message(s)", len(backfill))

    await db.set_state("last_sync:messages", datetime.now().isoformat())
    await db.commit()

    if run_id is not None:
        await db.record_section(run_id, "messages", "ok", item_count=len(messages))

    return new_messages, is_first, None


async def sync_all(
    client: VulcanClient,
    db: Database,
) -> FullSyncResult:
    """Sync all students and messages. Returns combined result."""
    # Clean up rows abandoned by a killed sync before opening a new one, so a
    # crash-looping container doesn't accumulate phantom 'running' rows.
    interrupted = await db.reconcile_stale_runs()
    if interrupted:
        logger.warning("Marked %d abandoned sync run(s) as interrupted", interrupted)
    await db.prune_sync_runs(keep_days=settings.sync_history_keep_days)

    run_id = await db.create_sync_run()
    errors = 0
    items = 0

    try:
        students = await client.get_students()
        if not students:
            # The roster is never legitimately empty for this account; an empty one
            # means the Context response changed shape or the profile is suspended.
            logger.error("No students found in account - treating as a failed sync")
            await db.complete_sync_run(
                run_id, "failed", 0, 0, 1, "roster empty: no active students returned"
            )
            return FullSyncResult(student_results=[])

        # Vulcan's current roster is the source of truth for which keys are live.
        # Do this before syncing so a mid-loop failure still leaves the flags right.
        retired = await db.deactivate_students_except({s.key for s in students})
        if retired:
            logger.info("Retired %d student row(s) from a previous school year", retired)
            await db.commit()

        student_results: list[SyncResult] = []
        failures: list[str] = []
        for student in students:
            logger.info("Syncing %s (%s)...", student.name, student.class_name)
            result = await sync_student(client, db, student, run_id=run_id)
            student_results.append(result)
            items += len(result.all_changes)
            for section, section_error in result.failed_sections.items():
                errors += 1
                failures.append(f"{student.name}/{section}: {section_error}")

        # Sync messages (unified inbox, once for all students)
        logger.info("Syncing messages...")
        new_messages, is_first_msg, msg_failure = await sync_messages(client, db, run_id=run_id)
        items += len(new_messages)
        if msg_failure:
            errors += 1
            failures.append(f"messages: {msg_failure}")

        # 'completed' must mean every section came back. A run where the scraper
        # fetched nothing used to land here as completed/errors_count=0, which is
        # exactly what made an outage look like a quiet week.
        status = "degraded" if failures else "completed"
        detail = "; ".join(failures)[:2000] if failures else None
        if failures:
            logger.error("Sync degraded: %d section failure(s): %s", errors, detail)

        await db.complete_sync_run(run_id, status, len(students), items, errors, detail)

        return FullSyncResult(
            student_results=student_results,
            new_messages=new_messages,
            is_first_message_sync=is_first_msg,
        )
    except Exception as exc:
        await db.complete_sync_run(run_id, "failed", 0, items, errors + 1, str(exc))
        raise
