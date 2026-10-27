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
    diff_days_off,
    diff_exams,
    diff_excuses,
    diff_feed,
    diff_grades,
    diff_homework,
    diff_schedule,
)
from vulcan_notify.drift import Drift, reconcile

if TYPE_CHECKING:
    from vulcan_notify.client import VulcanClient
    from vulcan_notify.db import Database
    from vulcan_notify.models import Grade, Message, Student

logger = logging.getLogger(__name__)

_MESSAGE_PAGE = 50
# Cap on pages of genuinely new messages per run (250). Past that something is off.
_MAX_NEW_MESSAGE_PAGES = 5


@dataclass
class SyncResult:
    """Result of a single sync cycle for one student."""

    student: Student
    new_grades: list[Change] = field(default_factory=list)
    new_attendance: list[Change] = field(default_factory=list)
    new_exams: list[Change] = field(default_factory=list)
    new_homework: list[Change] = field(default_factory=list)
    new_substitutions: list[Change] = field(default_factory=list)
    new_days_off: list[Change] = field(default_factory=list)
    new_excuses: list[Change] = field(default_factory=list)
    new_remarks: list[Change] = field(default_factory=list)
    new_announcements: list[Change] = field(default_factory=list)
    unread_messages: int = 0
    is_first_sync: bool = False
    failed_sections: dict[str, str] = field(default_factory=dict)

    @property
    def has_failures(self) -> bool:
        return bool(self.failed_sections)

    @property
    def has_changes(self) -> bool:
        return bool(self.all_changes)

    @property
    def all_changes(self) -> list[Change]:
        return (
            self.new_grades
            + self.new_attendance
            + self.new_exams
            + self.new_homework
            + self.new_substitutions
            + self.new_days_off
            + self.new_excuses
            + self.new_remarks
            + self.new_announcements
        )


@dataclass
class FullSyncResult:
    """Result of a full sync cycle (all students + messages)."""

    student_results: list[SyncResult]
    new_messages: list[Message] = field(default_factory=list)
    is_first_message_sync: bool = False
    drifts: list[Drift] = field(default_factory=list)


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
                elif (
                    existing.superseded_by_grade_id is not None
                    and grade.superseded_by_grade_id is None
                ):
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

    # ── Days off ─────────────────────────────────────────────────
    # Sections added after a student's first sync baseline on their own first
    # success, or every existing item would arrive as a push at once.
    async def section_is_new(section: str) -> bool:
        return await db.get_state(f"baseline:{student.key}:{section}") is None

    async def section_baselined(section: str, item_count: int) -> None:
        await db.set_state(f"baseline:{student.key}:{section}", datetime.now().isoformat())
        await section_ok(section, item_count)

    try:
        now = datetime.now()
        year = now.year if now.month >= 9 else now.year - 1
        days_off = await client.get_days_off(
            student, f"{year}-09-01T00:00:00.000Z", f"{year + 1}-08-31T23:59:59.999Z"
        )
        if not is_first and not await section_is_new("days_off"):
            result.new_days_off = await diff_days_off(student, days_off, db)
        for day in days_off:
            await db.upsert_day_off(student.key, day)
        await section_baselined("days_off", len(days_off))
    except Exception as exc:
        await section_failed("days_off", exc)

    # ── Excuses ──────────────────────────────────────────────────
    try:
        now = datetime.now()
        excuses = await client.get_excuses(
            student,
            (now - timedelta(days=settings.sync_attendance_days)).strftime(
                "%Y-%m-%dT00:00:00.000Z"
            ),
            now.strftime("%Y-%m-%dT23:59:59.999Z"),
        )
        if not is_first and not await section_is_new("excuses"):
            result.new_excuses = await diff_excuses(student, excuses, db)
        for excuse in excuses:
            await db.upsert_excuse(student.key, excuse)
        await section_baselined("excuses", len(excuses))
    except Exception as exc:
        await section_failed("excuses", exc)

    # ── Remarks / announcements ──────────────────────────────────
    for section, fetch, target in (
        ("remarks", client.get_remarks, result.new_remarks),
        ("announcements", client.get_announcements, result.new_announcements),
    ):
        try:
            items = await fetch(student)
            if not is_first and not await section_is_new(section):
                target.extend(await diff_feed(student, items, db))
            for item in items:
                await db.upsert_feed_item(student.key, item)
            await section_baselined(section, len(items))
        except Exception as exc:
            await section_failed(section, exc)

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
        messages = await client.get_messages(page_size=_MESSAGE_PAGE)
        # More than a page arrived since the last sync: keep paging while every
        # message on the page is new, so none of them is silently skipped.
        known_ids = await db.get_message_ids()
        page, full = messages, len(messages) == _MESSAGE_PAGE
        for _ in range(_MAX_NEW_MESSAGE_PAGES - 1):
            if is_first or not full or any(m.id in known_ids for m in page):
                break
            cursor = page[-1].id
            raw = await client.get_messages(_MESSAGE_PAGE, last_id=cursor)
            full = len(raw) == _MESSAGE_PAGE
            page = [m for m in raw if m.id < cursor]
            if not page:
                break
            messages.extend(page)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"[:500]
        logger.exception("Failed to fetch messages")
        if run_id is not None:
            await db.record_section(run_id, "messages", "failed", error_detail=detail)
        return [], is_first, detail

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

    await _backfill_message_history(client, db, messages)

    await db.set_state("last_sync:messages", datetime.now().isoformat())
    await db.commit()

    if run_id is not None:
        await db.record_section(run_id, "messages", "ok", item_count=len(messages))

    return new_messages, is_first, None


async def _backfill_message_history(
    client: VulcanClient, db: Database, latest: list[Message]
) -> None:
    """Walk the inbox back a few pages per run until the oldest message is stored.

    Only the newest page used to be fetched, so everything older than the 50 most
    recent messages was never seen. These are history, not news: stored, never pushed.
    """
    if await db.get_state("messages_history_done"):
        return
    cursor_state = await db.get_state("messages_history_cursor")
    cursor = int(cursor_state) if cursor_state else min((m.id for m in latest), default=0)
    if not cursor:
        return
    try:
        for _ in range(settings.sync_message_history_pages):
            page = [
                m for m in await client.get_messages(_MESSAGE_PAGE, last_id=cursor) if m.id < cursor
            ]
            if not page:
                await db.set_state("messages_history_done", datetime.now().isoformat())
                logger.info("Message history backfill complete")
                break
            for msg in page:
                await db.upsert_message(msg)
            cursor = min(m.id for m in page)
            await db.set_state("messages_history_cursor", str(cursor))
        await db.commit()
    except Exception:
        # History is best-effort; the newest page already succeeded.
        logger.exception("Message history backfill failed at cursor %d", cursor)


# Sections where zero items after a run of non-zero is implausible. Exams and homework
# genuinely drop to zero; attendance and schedule do so only over a break.
_NEVER_EMPTY = ("grades", "messages")
_EMPTY_ON_BREAKS = ("attendance", "schedule")


async def detect_api_drift(client: VulcanClient, db: Database, run_id: int) -> list[Drift]:
    """Compare this run's response shapes and item counts against what was learned.

    Never raises: drift detection is a watchdog, and a bug in it must not fail a sync
    that otherwise fetched everything.
    """
    try:
        drifts: list[Drift] = []
        known = await db.get_api_shapes()
        for endpoint, seen in client.shapes.items():
            outcome = reconcile(endpoint, known.get(endpoint, {}), seen)
            drifts.extend(outcome.drifts)
            if outcome.changed:
                await db.save_api_shape(endpoint, outcome.known)

        on_break = await db.is_day_off(datetime.now().strftime("%Y-%m-%d"))
        cursor = await db.db.execute(
            "SELECT student_key, section FROM sync_sections "
            "WHERE run_id = ? AND status = 'ok' AND item_count = 0",
            (run_id,),
        )
        for student_key, section in await cursor.fetchall():
            if section not in _NEVER_EMPTY and (section not in _EMPTY_ON_BREAKS or on_break):
                continue
            previous = await db.get_previous_item_counts(student_key, section, run_id)
            if len(previous) == 3 and all(previous):
                drifts.append(
                    Drift(
                        section,
                        "empty",
                        "",
                        f"0 items after {', '.join(map(str, previous))} in the last 3 runs",
                    )
                )

        if drifts:
            await db.record_drifts(drifts, run_id)
            for drift in drifts:
                logger.warning("API drift: %s", drift.describe())
        await db.commit()
        return drifts
    except Exception:
        logger.exception("Drift detection failed")
        return []


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

        drifts = await detect_api_drift(client, db, run_id)

        await db.complete_sync_run(run_id, status, len(students), items, errors, detail)

        return FullSyncResult(
            student_results=student_results,
            new_messages=new_messages,
            is_first_message_sync=is_first_msg,
            drifts=drifts,
        )
    except Exception as exc:
        await db.complete_sync_run(run_id, "failed", 0, items, errors + 1, str(exc))
        raise
