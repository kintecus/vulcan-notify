"""Integration tests for the sync pipeline."""

from unittest.mock import AsyncMock

from vulcan_notify.db import Database
from vulcan_notify.models import (
    ClassificationPeriod,
    DashboardData,
    Exam,
    Grade,
    Homework,
    Student,
)
from vulcan_notify.sync import sync_all, sync_student

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
    client.get_grades = AsyncMock(return_value=[] if grades is None else grades)
    client.get_attendance = AsyncMock(return_value=[])
    client.get_exams = AsyncMock(return_value=[] if exams is None else exams)
    client.get_homework = AsyncMock(return_value=[] if homework is None else homework)
    client.get_dashboard = AsyncMock(return_value=DashboardData(unread_messages=5))
    client.close = AsyncMock()
    return client


async def test_first_sync_stores_baseline(db: Database) -> None:
    """First sync should store data but report no changes."""
    client = _make_mock_client(grades=[GRADE], exams=[EXAM])
    result = await sync_student(client, db, STUDENT_A)

    assert result.is_first_sync is True
    assert result.has_changes is False
    assert result.unread_messages == 5

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
    results = await sync_all(client, db)

    assert len(results) == 2
    assert results[0].student.name == "Jan"
    assert results[1].student.name == "Anna"


async def test_sync_all_no_students(db: Database) -> None:
    """sync_all with no students returns empty list."""
    client = _make_mock_client(students=[])
    results = await sync_all(client, db)
    assert results == []
