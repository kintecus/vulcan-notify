"""Tests for the database schema and operations."""

from vulcan_notify.db import Database
from vulcan_notify.models import AttendanceEntry, Exam, Grade, Homework, Student


async def test_schema_creation(db: Database) -> None:
    """Verify all tables are created."""
    cursor = await db.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in await cursor.fetchall()]
    assert "students" in tables
    assert "grades" in tables
    assert "attendance" in tables
    assert "exams" in tables
    assert "homework" in tables
    assert "messages" in tables
    assert "sync_state" in tables


async def test_upsert_student(db: Database) -> None:
    student = Student(
        key="KEY1", name="Jan", class_name="3A",
        school="Szkola", diary_id=1001, mailbox_key="aaa",
    )
    await db.upsert_student(student)
    await db.upsert_student(student)  # idempotent

    cursor = await db.db.execute("SELECT COUNT(*) FROM students")
    count = (await cursor.fetchone())[0]
    assert count == 1


async def test_upsert_student_updates_name(db: Database) -> None:
    student = Student(
        key="KEY1", name="Jan", class_name="3A",
        school="Szkola", diary_id=1001, mailbox_key="aaa",
    )
    await db.upsert_student(student)

    student.name = "Jan Updated"
    await db.upsert_student(student)

    cursor = await db.db.execute("SELECT name FROM students WHERE key = 'KEY1'")
    row = await cursor.fetchone()
    assert row[0] == "Jan Updated"


async def test_upsert_grade_idempotent(db: Database) -> None:
    student = Student(
        key="KEY1", name="Jan", class_name="3A",
        school="Szkola", diary_id=1001, mailbox_key="aaa",
    )
    await db.upsert_student(student)

    grade = Grade(
        column_id=100, value="5", date="15.03.2026", subject="Math",
        column_name="Test 1", category="Biezace",
        weight=1, teacher="Nowak", changed_since_login=False,
    )
    await db.upsert_grade("KEY1", grade)
    await db.upsert_grade("KEY1", grade)

    cursor = await db.db.execute("SELECT COUNT(*) FROM grades")
    count = (await cursor.fetchone())[0]
    assert count == 1


async def test_upsert_grade_updates_value(db: Database) -> None:
    student = Student(
        key="KEY1", name="Jan", class_name="3A",
        school="Szkola", diary_id=1001, mailbox_key="aaa",
    )
    await db.upsert_student(student)

    grade = Grade(
        column_id=100, value="4", date="15.03.2026", subject="Math",
        column_name="Test 1", category="Biezace",
        weight=1, teacher="Nowak", changed_since_login=False,
    )
    await db.upsert_grade("KEY1", grade)

    grade.value = "5"
    await db.upsert_grade("KEY1", grade)

    rows = await db.get_grades_for_student("KEY1")
    assert len(rows) == 1
    assert rows[0]["value"] == "5"


async def test_get_grades_for_student(db: Database) -> None:
    student = Student(
        key="KEY1", name="Jan", class_name="3A",
        school="Szkola", diary_id=1001, mailbox_key="aaa",
    )
    await db.upsert_student(student)

    for i in range(3):
        grade = Grade(
            column_id=100 + i, value=str(3 + i), date="15.03.2026", subject="Math",
            column_name=f"Test {i}", category="Biezace",
            weight=1, teacher="Nowak", changed_since_login=False,
        )
        await db.upsert_grade("KEY1", grade)

    rows = await db.get_grades_for_student("KEY1")
    assert len(rows) == 3

    # Different student has no grades
    rows2 = await db.get_grades_for_student("KEY2")
    assert len(rows2) == 0


async def test_upsert_attendance(db: Database) -> None:
    student = Student(
        key="KEY1", name="Jan", class_name="3A",
        school="Szkola", diary_id=1001, mailbox_key="aaa",
    )
    await db.upsert_student(student)

    entry = AttendanceEntry(
        lesson_number=3, category=2, date="2026-03-14", subject="Math",
        teacher="Nowak", time_from="09:50", time_to="10:35",
    )
    await db.upsert_attendance("KEY1", entry)
    await db.upsert_attendance("KEY1", entry)  # idempotent

    rows = await db.get_attendance_for_student("KEY1")
    assert len(rows) == 1
    assert rows[0]["category"] == 2


async def test_upsert_exam(db: Database) -> None:
    student = Student(
        key="KEY1", name="Jan", class_name="3A",
        school="Szkola", diary_id=1001, mailbox_key="aaa",
    )
    await db.upsert_student(student)

    exam = Exam(id=10001, date="2026-03-16", subject="Przyroda", type=2)
    await db.upsert_exam("KEY1", exam)
    await db.upsert_exam("KEY1", exam)  # idempotent

    cursor = await db.db.execute("SELECT COUNT(*) FROM exams")
    count = (await cursor.fetchone())[0]
    assert count == 1


async def test_upsert_homework(db: Database) -> None:
    student = Student(
        key="KEY1", name="Jan", class_name="3A",
        school="Szkola", diary_id=1001, mailbox_key="aaa",
    )
    await db.upsert_student(student)

    hw = Homework(id=10002, date="2026-03-16", subject="Plastyka")
    await db.upsert_homework("KEY1", hw)
    await db.upsert_homework("KEY1", hw)  # idempotent

    cursor = await db.db.execute("SELECT COUNT(*) FROM homework")
    count = (await cursor.fetchone())[0]
    assert count == 1


async def test_sync_state(db: Database) -> None:
    assert await db.get_state("last_sync") is None

    await db.set_state("last_sync", "2026-03-15T12:00:00")
    assert await db.get_state("last_sync") == "2026-03-15T12:00:00"

    await db.set_state("last_sync", "2026-03-15T13:00:00")
    assert await db.get_state("last_sync") == "2026-03-15T13:00:00"


async def test_migration_drops_legacy_tables(tmp_path) -> None:
    """Verify old schema tables are dropped during migration."""
    import aiosqlite

    db_path = tmp_path / "legacy.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE seen_items (item_type TEXT, item_id TEXT, item_hash TEXT)")
        await conn.execute("CREATE TABLE poll_state (key TEXT, value TEXT)")
        await conn.commit()

    db = Database(db_path)
    await db.connect()

    cursor = await db.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('seen_items', 'poll_state')"
    )
    legacy = await cursor.fetchall()
    assert len(legacy) == 0

    # New tables should exist
    cursor = await db.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='students'"
    )
    assert await cursor.fetchone() is not None

    await db.close()
