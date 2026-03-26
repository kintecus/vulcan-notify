"""Tests for macOS Calendar integration."""

from unittest.mock import AsyncMock, patch

from vulcan_notify.calendar import (
    CalendarSyncResult,
    _escape_applescript,
    _event_body,
    _exam_title,
    _homework_title,
    _parse_date,
    sync_to_calendar,
)
from vulcan_notify.db import Database
from vulcan_notify.models import Exam, Homework, Student

STUDENT = Student(
    key="KEY1",
    name="Alice Smith",
    class_name="4B",
    school="Szkola",
    diary_id=1001,
    mailbox_key="aaa",
)

EXAM = Exam(
    id=100,
    date="2026-03-25T00:00:00+01:00",
    subject="Matematyka",
    type=2,
    description="Test z mnozenia",
    teacher="Kowalski Jan",
)

HOMEWORK = Homework(
    id=200,
    date="2026-03-26T00:00:00+01:00",
    subject="Plastyka",
    content="Przyniesc blok rysunkowy",
    teacher="Nowak Anna",
)


# ── Unit tests for helpers ────────────────────────────────────────


def test_parse_date_iso() -> None:
    assert _parse_date("2026-03-25T00:00:00+01:00") == "2026-03-25"


def test_parse_date_plain() -> None:
    assert _parse_date("2026-03-25") == "2026-03-25"


def test_parse_date_invalid() -> None:
    assert _parse_date("not-a-date") == "not-a-date"


def test_exam_title_quiz() -> None:
    assert _exam_title("Matematyka", 2) == "Kartkowka - Matematyka"


def test_exam_title_test() -> None:
    assert _exam_title("Historia", 1) == "Sprawdzian - Historia"


def test_exam_title_unknown() -> None:
    assert _exam_title("Fizyka", 99) == "Sprawdzian/kartkowka - Fizyka"


def test_homework_title() -> None:
    assert _homework_title("Plastyka") == "Zadanie domowe - Plastyka"


def test_event_body_full() -> None:
    body = _event_body("Do page 5", "Kowalski")
    assert "Do page 5" in body
    assert "Nauczyciel: Kowalski" in body


def test_event_body_no_description() -> None:
    body = _event_body(None, "Kowalski")
    assert body == "Nauczyciel: Kowalski"


def test_event_body_no_teacher() -> None:
    body = _event_body("Do page 5", None)
    assert body == "Do page 5"


def test_event_body_empty() -> None:
    assert _event_body(None, None) == ""


def test_escape_applescript() -> None:
    assert _escape_applescript('He said "hi"') == 'He said \\"hi\\"'
    assert _escape_applescript("back\\slash") == "back\\\\slash"


# ── DB integration tests ─────────────────────────────────────────


async def test_calendar_uid_column_exists(db: Database) -> None:
    """Verify calendar_uid column exists on exams and homework tables."""
    for table in ("exams", "homework"):
        cursor = await db.db.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in await cursor.fetchall()}
        assert "calendar_uid" in columns


async def test_set_and_clear_calendar_uid(db: Database) -> None:
    await db.upsert_student(STUDENT)
    await db.upsert_exam(STUDENT.key, EXAM)
    await db.commit()

    await db.set_calendar_uid("exams", EXAM.id, "UID-123")
    await db.commit()

    cursor = await db.db.execute(
        "SELECT calendar_uid FROM exams WHERE id = ?", (EXAM.id,)
    )
    assert (await cursor.fetchone())[0] == "UID-123"

    await db.clear_calendar_uid("exams", EXAM.id)
    await db.commit()

    cursor = await db.db.execute(
        "SELECT calendar_uid FROM exams WHERE id = ?", (EXAM.id,)
    )
    assert (await cursor.fetchone())[0] is None


async def test_get_items_for_calendar(db: Database) -> None:
    await db.upsert_student(STUDENT)
    await db.upsert_exam(STUDENT.key, EXAM)
    await db.upsert_homework(STUDENT.key, HOMEWORK)
    await db.commit()

    items = await db.get_items_for_calendar(STUDENT.key)
    assert len(items["exams"]) == 1
    assert len(items["homework"]) == 1
    assert items["exams"][0]["subject"] == "Matematyka"
    assert items["homework"][0]["subject"] == "Plastyka"


async def test_get_items_excludes_soft_deleted(db: Database) -> None:
    await db.upsert_student(STUDENT)
    await db.upsert_exam(STUDENT.key, EXAM)
    await db.commit()

    # Soft-delete by marking with a different set of IDs
    await db.mark_missing(STUDENT.key, "exams", {999})
    await db.commit()

    items = await db.get_items_for_calendar(STUDENT.key)
    assert len(items["exams"]) == 0


async def test_get_deleted_items_with_calendar_uid(db: Database) -> None:
    await db.upsert_student(STUDENT)
    await db.upsert_exam(STUDENT.key, EXAM)
    await db.set_calendar_uid("exams", EXAM.id, "UID-456")
    await db.commit()

    # Soft-delete by marking with a different set of IDs
    await db.mark_missing(STUDENT.key, "exams", {999})
    await db.commit()

    deleted = await db.get_deleted_items_with_calendar_uid(STUDENT.key)
    assert len(deleted["exams"]) == 1
    assert deleted["exams"][0]["calendar_uid"] == "UID-456"


async def test_clear_all_calendar_uids(db: Database) -> None:
    await db.upsert_student(STUDENT)
    await db.upsert_exam(STUDENT.key, EXAM)
    await db.upsert_homework(STUDENT.key, HOMEWORK)
    await db.set_calendar_uid("exams", EXAM.id, "UID-1")
    await db.set_calendar_uid("homework", HOMEWORK.id, "UID-2")
    await db.commit()

    await db.clear_all_calendar_uids()

    cursor = await db.db.execute(
        "SELECT calendar_uid FROM exams WHERE id = ?", (EXAM.id,)
    )
    assert (await cursor.fetchone())[0] is None
    cursor = await db.db.execute(
        "SELECT calendar_uid FROM homework WHERE id = ?", (HOMEWORK.id,)
    )
    assert (await cursor.fetchone())[0] is None


# ── sync_to_calendar integration tests (mocked AppleScript) ──────


@patch("vulcan_notify.calendar.settings")
@patch("vulcan_notify.calendar._run_applescript")
async def test_sync_creates_events(
    mock_applescript: AsyncMock,
    mock_settings: AsyncMock,
    db: Database,
) -> None:
    mock_settings.calendar_map = {"Alice Smith": "School Alice"}
    mock_settings.calendar_reminder_hours = 24
    mock_applescript.return_value = "NEW-UID-1"

    await db.upsert_student(STUDENT)
    await db.upsert_exam(STUDENT.key, EXAM)
    await db.upsert_homework(STUDENT.key, HOMEWORK)
    await db.commit()

    result = await sync_to_calendar(db)

    assert result.created == 2
    assert result.updated == 0
    assert result.errors == 0

    # Verify UIDs stored
    cursor = await db.db.execute(
        "SELECT calendar_uid FROM exams WHERE id = ?", (EXAM.id,)
    )
    assert (await cursor.fetchone())[0] == "NEW-UID-1"


@patch("vulcan_notify.calendar.settings")
@patch("vulcan_notify.calendar._run_applescript")
async def test_sync_updates_existing_events(
    mock_applescript: AsyncMock,
    mock_settings: AsyncMock,
    db: Database,
) -> None:
    mock_settings.calendar_map = {"Alice Smith": "School Alice"}
    mock_settings.calendar_reminder_hours = 24
    mock_applescript.return_value = ""

    await db.upsert_student(STUDENT)
    await db.upsert_exam(STUDENT.key, EXAM)
    await db.set_calendar_uid("exams", EXAM.id, "EXISTING-UID")
    await db.commit()

    result = await sync_to_calendar(db)

    assert result.updated == 1
    assert result.created == 0


@patch("vulcan_notify.calendar.settings")
@patch("vulcan_notify.calendar._run_applescript")
async def test_sync_deletes_soft_deleted_events(
    mock_applescript: AsyncMock,
    mock_settings: AsyncMock,
    db: Database,
) -> None:
    mock_settings.calendar_map = {"Alice Smith": "School Alice"}
    mock_settings.calendar_reminder_hours = 24
    mock_applescript.return_value = ""

    await db.upsert_student(STUDENT)
    await db.upsert_exam(STUDENT.key, EXAM)
    await db.set_calendar_uid("exams", EXAM.id, "TO-DELETE-UID")
    await db.commit()

    # Soft-delete by marking with a different set of IDs
    await db.mark_missing(STUDENT.key, "exams", {999})
    await db.commit()

    result = await sync_to_calendar(db)

    assert result.deleted == 1

    # UID should be cleared
    cursor = await db.db.execute(
        "SELECT calendar_uid FROM exams WHERE id = ?", (EXAM.id,)
    )
    assert (await cursor.fetchone())[0] is None


@patch("vulcan_notify.calendar.settings")
async def test_sync_skips_unmapped_students(
    mock_settings: AsyncMock,
    db: Database,
) -> None:
    mock_settings.calendar_map = {}  # no mapping

    result = await sync_to_calendar(db)

    assert result == CalendarSyncResult()


@patch("vulcan_notify.calendar.settings")
@patch("vulcan_notify.calendar._run_applescript")
async def test_sync_skips_student_without_mapping(
    mock_applescript: AsyncMock,
    mock_settings: AsyncMock,
    db: Database,
) -> None:
    mock_settings.calendar_map = {"Other Student": "Other Calendar"}
    mock_settings.calendar_reminder_hours = 24

    await db.upsert_student(STUDENT)
    await db.upsert_exam(STUDENT.key, EXAM)
    await db.commit()

    result = await sync_to_calendar(db)

    assert result.created == 0
    assert "Alice Smith" in result.skipped_students
    mock_applescript.assert_not_called()


@patch("vulcan_notify.calendar.settings")
@patch("vulcan_notify.calendar._run_applescript")
async def test_sync_handles_applescript_error(
    mock_applescript: AsyncMock,
    mock_settings: AsyncMock,
    db: Database,
) -> None:
    mock_settings.calendar_map = {"Alice Smith": "School Alice"}
    mock_settings.calendar_reminder_hours = 24
    mock_applescript.side_effect = RuntimeError("AppleScript failed")

    await db.upsert_student(STUDENT)
    await db.upsert_exam(STUDENT.key, EXAM)
    await db.commit()

    result = await sync_to_calendar(db)

    assert result.errors == 1
    assert result.created == 0
