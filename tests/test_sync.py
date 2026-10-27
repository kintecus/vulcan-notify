"""Integration tests for the sync pipeline."""

from unittest.mock import AsyncMock

from vulcan_notify.db import Database
from vulcan_notify.models import (
    ClassificationPeriod,
    Exam,
    Grade,
    Homework,
    Message,
    Student,
)
from vulcan_notify.sync import sync_all, sync_messages, sync_student

STUDENT_A = Student(
    key="KEYA",
    name="Jan",
    class_name="3A",
    school="Szkola",
    diary_id=1001,
    mailbox_key="aaa",
)
STUDENT_B = Student(
    key="KEYB",
    name="Anna",
    class_name="5B",
    school="Szkola",
    diary_id=1002,
    mailbox_key="bbb",
)

PERIOD = ClassificationPeriod(id=1, number=2, date_from="2026-02-01", date_to="2026-08-31")

GRADE = Grade(
    column_id=100,
    value="5",
    date="15.03.2026",
    subject="Math",
    column_name="Sprawdzian 1",
    category="Biezace",
    weight=2,
    teacher="Nowak A.",
    changed_since_login=False,
)

EXAM = Exam(id=10001, date="2026-03-16", subject="Przyroda", type=2)
HOMEWORK = Homework(id=10002, date="2026-03-16", subject="Plastyka")


def _make_mock_client(
    students: list[Student] | None = None,
    grades: list[Grade] | None = None,
    exams: list[Exam] | None = None,
    homework: list[Homework] | None = None,
) -> AsyncMock:
    client = AsyncMock()
    client.get_students = AsyncMock(return_value=[STUDENT_A] if students is None else students)
    client.get_periods = AsyncMock(return_value=[PERIOD])
    grade_list = [] if grades is None else grades
    client.get_grades = AsyncMock(return_value=grade_list)
    client.get_grades_and_summaries = AsyncMock(return_value=(grade_list, []))
    client.get_attendance = AsyncMock(return_value=[])
    client.get_exams = AsyncMock(return_value=[] if exams is None else exams)
    client.get_homework = AsyncMock(return_value=[] if homework is None else homework)
    client.get_schedule = AsyncMock(return_value=[])
    client.get_days_off = AsyncMock(return_value=[])
    client.get_excuses = AsyncMock(return_value=[])
    client.get_remarks = AsyncMock(return_value=[])
    client.get_announcements = AsyncMock(return_value=[])
    client.shapes = {}
    client.get_messages = AsyncMock(return_value=[])
    client.get_message_detail = AsyncMock(return_value=None)
    client.close = AsyncMock()
    return client


async def test_first_sync_stores_baseline(db: Database) -> None:
    """First sync should store data but report no changes."""
    client = _make_mock_client(grades=[GRADE], exams=[EXAM])
    result = await sync_student(client, db, STUDENT_A)

    assert result.is_first_sync is True
    assert result.has_changes is False

    # Data should be stored
    rows = await db.get_grades_for_student("KEYA")
    assert len(rows) == 1
    assert rows[0]["value"] == "5"


async def test_second_sync_no_changes(db: Database) -> None:
    """Second sync with same data should report no changes."""
    client = _make_mock_client(grades=[GRADE])

    # First sync (baseline)
    await sync_student(client, db, STUDENT_A)

    # Second sync (same data)
    result = await sync_student(client, db, STUDENT_A)

    assert result.is_first_sync is False
    assert result.has_changes is False


async def test_new_grade_detected_on_second_sync(db: Database) -> None:
    """New grade appearing after baseline should be detected."""
    # First sync with no grades
    client = _make_mock_client(grades=[])
    await sync_student(client, db, STUDENT_A)

    # Second sync with a new grade
    client = _make_mock_client(grades=[GRADE])
    result = await sync_student(client, db, STUDENT_A)

    assert result.is_first_sync is False
    assert len(result.new_grades) == 1
    assert result.new_grades[0].change_type == "new"
    assert "5" in result.new_grades[0].title


async def test_changed_grade_detected(db: Database) -> None:
    """Changed grade value should be detected as update."""
    grade_v1 = Grade(
        column_id=100,
        value="4",
        date="15.03.2026",
        subject="Math",
        column_name="Sprawdzian 1",
        category="Biezace",
        weight=2,
        teacher="Nowak A.",
        changed_since_login=False,
    )

    # First sync with grade=4
    client = _make_mock_client(grades=[grade_v1])
    await sync_student(client, db, STUDENT_A)

    # Second sync with grade=5
    client = _make_mock_client(grades=[GRADE])  # GRADE has value="5"
    result = await sync_student(client, db, STUDENT_A)

    assert len(result.new_grades) == 1
    assert result.new_grades[0].change_type == "updated"
    assert "4" in result.new_grades[0].title
    assert "5" in result.new_grades[0].title


async def test_new_exam_detected(db: Database) -> None:
    """New exam should be detected after baseline."""
    client = _make_mock_client(exams=[])
    await sync_student(client, db, STUDENT_A)

    client = _make_mock_client(exams=[EXAM])
    result = await sync_student(client, db, STUDENT_A)

    assert len(result.new_exams) == 1
    assert "Przyroda" in result.new_exams[0].title


async def test_sync_all_multiple_students(db: Database) -> None:
    """sync_all should return separate results per student."""
    client = _make_mock_client(students=[STUDENT_A, STUDENT_B])
    full = await sync_all(client, db)

    assert len(full.student_results) == 2
    assert full.student_results[0].student.name == "Jan"
    assert full.student_results[1].student.name == "Anna"


async def test_sync_all_no_students(db: Database) -> None:
    """sync_all with no students returns empty FullSyncResult."""
    client = _make_mock_client(students=[])
    full = await sync_all(client, db)
    assert full.student_results == []


async def test_sync_messages_backfills_legacy_content(db: Database) -> None:
    """Messages stored before detail-fetch was added get content backfilled."""
    # Seed a legacy message row directly: content NULL, api_global_key present.
    legacy = Message(
        id=9001,
        api_global_key="legacy-key",
        sender="Teacher A.",
        subject="Old note",
        date="2026-03-01",
        mailbox="Jan",
        has_attachments=False,
        is_read=True,
    )
    await db.upsert_message(legacy)
    await db.commit()
    # Mark "messages" as not-first-sync so we only exercise the backfill branch.
    await db.set_state("last_sync:messages", "2026-03-01T00:00:00")

    client = _make_mock_client()
    client.get_messages = AsyncMock(return_value=[])  # no new messages
    client.get_message_detail = AsyncMock(return_value="<p>Backfilled body.</p>")

    await sync_messages(client, db)

    client.get_message_detail.assert_awaited_with("legacy-key")
    cursor = await db.db.execute("SELECT content FROM messages WHERE id = 9001")
    row = await cursor.fetchone()
    assert row[0] == "<p>Backfilled body.</p>"


def _msg(msg_id: int) -> Message:
    return Message(
        id=msg_id,
        api_global_key=f"k{msg_id}",
        sender="Teacher",
        subject=f"S{msg_id}",
        date="2026-10-01",
        mailbox="Jan",
        has_attachments=False,
        is_read=False,
    )


def _inbox(ids: list[int]):
    """Fake Odebrane: newest first, inclusive cursor, like the real endpoint."""
    ordered = sorted(ids, reverse=True)

    async def get_messages(page_size: int = 50, last_id: int = 0) -> list[Message]:
        older = [i for i in ordered if last_id == 0 or i <= last_id]
        return [_msg(i) for i in older[:page_size]]

    return get_messages


async def test_new_sections_baseline_on_first_success(db: Database) -> None:
    """Days off etc. added to an existing install must not push every stored item."""
    from vulcan_notify.models import DayOff

    client = _make_mock_client()
    await sync_student(client, db, STUDENT_A)  # first sync of the student
    # Simulate an install that predates the new sections.
    await db.db.execute("DELETE FROM sync_state WHERE key LIKE 'baseline:%:days_off'")
    await db.commit()

    client.get_days_off = AsyncMock(return_value=[DayOff("Ferie", "2027-01-19", "2027-02-01")])
    result = await sync_student(client, db, STUDENT_A)
    assert result.new_days_off == []

    client.get_days_off = AsyncMock(
        return_value=[
            DayOff("Ferie", "2027-01-19", "2027-02-01"),
            DayOff("Dzien dyrektorski", "2026-11-10", "2026-11-10"),
        ]
    )
    result = await sync_student(client, db, STUDENT_A)
    assert [c.title for c in result.new_days_off] == ["Day off: Dzien dyrektorski"]


async def test_excuse_status_change_detected(db: Database) -> None:
    from vulcan_notify.models import Excuse

    client = _make_mock_client()
    client.get_excuses = AsyncMock(return_value=[Excuse(7, 70, "2026-10-01", 3, 1)])
    await sync_student(client, db, STUDENT_A)
    await sync_student(client, db, STUDENT_A)  # section baseline done, no change

    client.get_excuses = AsyncMock(return_value=[Excuse(7, 70, "2026-10-01", 3, 2)])
    result = await sync_student(client, db, STUDENT_A)
    assert len(result.new_excuses) == 1
    assert "status 1 -> 2" in result.new_excuses[0].body


async def test_new_remark_detected(db: Database) -> None:
    from vulcan_notify.models import FeedItem

    client = _make_mock_client()
    await sync_student(client, db, STUDENT_A)
    client.get_remarks = AsyncMock(
        return_value=[FeedItem("remark", "r1", "Brak stroju", "Nowak", "2026-10-01", "{}")]
    )
    result = await sync_student(client, db, STUDENT_A)
    assert [c.body for c in result.new_remarks] == ["Nowak: Brak stroju"]


async def test_messages_page_until_known_when_more_than_a_page_is_new(db: Database) -> None:
    await db.upsert_message(_msg(1))
    await db.set_state("last_sync:messages", "2026-10-01T00:00:00")
    await db.set_state("messages_history_done", "x")
    await db.commit()

    client = _make_mock_client()
    client.get_messages = AsyncMock(side_effect=_inbox(list(range(1, 121))))
    new, is_first, failure = await sync_messages(client, db)

    assert failure is None and not is_first
    assert sorted(m.id for m in new) == list(range(2, 121))


async def test_message_history_backfills_older_pages_without_pushing(db: Database) -> None:
    client = _make_mock_client()
    client.get_messages = AsyncMock(side_effect=_inbox(list(range(1, 201))))

    # First message sync: baseline page plus a few history pages, nothing reported.
    new, is_first, _ = await sync_messages(client, db)
    assert is_first and new == []
    stored = await db.get_message_ids()
    # 50 newest + 3 history pages of 49 (each page repeats its cursor message)
    assert len(stored) == 197
    assert await db.get_state("messages_history_done") is None

    new, _, _ = await sync_messages(client, db)
    assert new == []
    assert await db.get_state("messages_history_done") is not None


async def test_drift_reported_in_full_sync_result(db: Database) -> None:
    from vulcan_notify.drift import extract

    client = _make_mock_client()
    client.shapes = {"uczen:Context": extract({"uczniowie": [{"key": "x"}]})}
    await sync_all(client, db)
    client.shapes = {"uczen:Context": extract({"uczniowie": [{"klucz": "x"}]})}
    result = await sync_all(client, db)
    assert {d.kind for d in result.drifts} == {"added", "missing"}
