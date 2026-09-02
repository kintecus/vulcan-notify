"""Tests for the database schema and operations."""

from vulcan_notify.db import Database
from vulcan_notify.models import AttendanceEntry, Exam, Grade, Homework, Student


async def test_schema_creation(db: Database) -> None:
    """Verify all tables are created."""
    cursor = await db.db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
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
        key="KEY1",
        name="Jan",
        class_name="3A",
        school="Szkola",
        diary_id=1001,
        mailbox_key="aaa",
    )
    await db.upsert_student(student)
    await db.upsert_student(student)  # idempotent

    cursor = await db.db.execute("SELECT COUNT(*) FROM students")
    count = (await cursor.fetchone())[0]
    assert count == 1


async def test_upsert_student_updates_name(db: Database) -> None:
    student = Student(
        key="KEY1",
        name="Jan",
        class_name="3A",
        school="Szkola",
        diary_id=1001,
        mailbox_key="aaa",
    )
    await db.upsert_student(student)

    student.name = "Jan Updated"
    await db.upsert_student(student)

    cursor = await db.db.execute("SELECT name FROM students WHERE key = 'KEY1'")
    row = await cursor.fetchone()
    assert row[0] == "Jan Updated"


async def test_upsert_grade_idempotent(db: Database) -> None:
    student = Student(
        key="KEY1",
        name="Jan",
        class_name="3A",
        school="Szkola",
        diary_id=1001,
        mailbox_key="aaa",
    )
    await db.upsert_student(student)

    grade = Grade(
        column_id=100,
        value="5",
        date="15.03.2026",
        subject="Math",
        column_name="Test 1",
        category="Biezace",
        weight=1,
        teacher="Nowak",
        changed_since_login=False,
    )
    await db.upsert_grade("KEY1", grade)
    await db.upsert_grade("KEY1", grade)

    cursor = await db.db.execute("SELECT COUNT(*) FROM grades")
    count = (await cursor.fetchone())[0]
    assert count == 1


async def test_upsert_grade_updates_value(db: Database) -> None:
    student = Student(
        key="KEY1",
        name="Jan",
        class_name="3A",
        school="Szkola",
        diary_id=1001,
        mailbox_key="aaa",
    )
    await db.upsert_student(student)

    grade = Grade(
        column_id=100,
        value="4",
        date="15.03.2026",
        subject="Math",
        column_name="Test 1",
        category="Biezace",
        weight=1,
        teacher="Nowak",
        changed_since_login=False,
    )
    await db.upsert_grade("KEY1", grade)

    grade.value = "5"
    await db.upsert_grade("KEY1", grade)

    rows = await db.get_grades_for_student("KEY1")
    assert len(rows) == 1
    assert rows[0]["value"] == "5"


async def test_get_grades_for_student(db: Database) -> None:
    student = Student(
        key="KEY1",
        name="Jan",
        class_name="3A",
        school="Szkola",
        diary_id=1001,
        mailbox_key="aaa",
    )
    await db.upsert_student(student)

    for i in range(3):
        grade = Grade(
            column_id=100 + i,
            value=str(3 + i),
            date="15.03.2026",
            subject="Math",
            column_name=f"Test {i}",
            category="Biezace",
            weight=1,
            teacher="Nowak",
            changed_since_login=False,
        )
        await db.upsert_grade("KEY1", grade)

    rows = await db.get_grades_for_student("KEY1")
    assert len(rows) == 3

    # Different student has no grades
    rows2 = await db.get_grades_for_student("KEY2")
    assert len(rows2) == 0


async def test_upsert_attendance(db: Database) -> None:
    student = Student(
        key="KEY1",
        name="Jan",
        class_name="3A",
        school="Szkola",
        diary_id=1001,
        mailbox_key="aaa",
    )
    await db.upsert_student(student)

    entry = AttendanceEntry(
        lesson_number=3,
        category=2,
        date="2026-03-14",
        subject="Math",
        teacher="Nowak",
        time_from="09:50",
        time_to="10:35",
    )
    await db.upsert_attendance("KEY1", entry)
    await db.upsert_attendance("KEY1", entry)  # idempotent

    rows = await db.get_attendance_for_student("KEY1")
    assert len(rows) == 1
    assert rows[0]["category"] == 2


async def test_upsert_exam(db: Database) -> None:
    student = Student(
        key="KEY1",
        name="Jan",
        class_name="3A",
        school="Szkola",
        diary_id=1001,
        mailbox_key="aaa",
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
        key="KEY1",
        name="Jan",
        class_name="3A",
        school="Szkola",
        diary_id=1001,
        mailbox_key="aaa",
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


def _student(key: str, name: str = "Solomiia", class_name: str = "4E") -> Student:
    return Student(
        key=key,
        name=name,
        class_name=class_name,
        school="Szkola",
        diary_id=1001,
        mailbox_key="aaa",
    )


async def _active_map(db: Database) -> dict[str, int]:
    cursor = await db.db.execute("SELECT key, active FROM students")
    return {row[0]: row[1] for row in await cursor.fetchall()}


async def test_new_student_is_active_by_default(db: Database) -> None:
    await db.upsert_student(_student("KEY1"))
    assert await _active_map(db) == {"KEY1": 1}


async def test_deactivate_students_except_retires_previous_year(db: Database) -> None:
    """A September rollover leaves last year's key behind; it should go inactive."""
    await db.upsert_student(_student("OLD", class_name="4E"))
    await db.upsert_student(_student("NEW", class_name="5E"))

    retired = await db.deactivate_students_except({"NEW"})

    assert retired == 1
    assert await _active_map(db) == {"OLD": 0, "NEW": 1}


async def test_deactivate_students_except_is_idempotent(db: Database) -> None:
    await db.upsert_student(_student("OLD"))
    await db.upsert_student(_student("NEW"))

    assert await db.deactivate_students_except({"NEW"}) == 1
    # Second run has nothing left to retire.
    assert await db.deactivate_students_except({"NEW"}) == 0


async def test_deactivate_students_except_ignores_empty_set(db: Database) -> None:
    """A failed roster fetch must not deactivate the whole table."""
    await db.upsert_student(_student("KEY1"))
    await db.upsert_student(_student("KEY2"))

    retired = await db.deactivate_students_except(set())

    assert retired == 0
    assert await _active_map(db) == {"KEY1": 1, "KEY2": 1}


async def test_upsert_student_reactivates_retired_key(db: Database) -> None:
    await db.upsert_student(_student("KEY1"))
    await db.deactivate_students_except({"OTHER"})
    assert (await _active_map(db))["KEY1"] == 0

    await db.upsert_student(_student("KEY1"))

    assert (await _active_map(db))["KEY1"] == 1


async def test_deactivating_preserves_history(db: Database) -> None:
    """Retiring a key must not touch the grades hanging off it."""
    await db.upsert_student(_student("OLD"))
    await db.upsert_grade(
        "OLD",
        Grade(
            column_id=100,
            value="5",
            date="10.05.2026",
            subject="Matematyka",
            column_name="Sprawdzian",
            category="Biezace",
            weight=3.0,
            teacher="Kowalska",
            changed_since_login=False,
        ),
    )

    await db.deactivate_students_except({"NEW"})

    cursor = await db.db.execute("SELECT COUNT(*) FROM grades WHERE student_key = 'OLD'")
    assert (await cursor.fetchone())[0] == 1


async def test_migration_adds_active_column(tmp_path) -> None:
    """An existing DB predating the active flag gets it, defaulting to active."""
    import aiosqlite

    db_path = tmp_path / "preactive.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "CREATE TABLE students ("
            "key TEXT PRIMARY KEY, name TEXT NOT NULL, class_name TEXT NOT NULL, "
            "school TEXT NOT NULL, diary_id INTEGER NOT NULL, mailbox_key TEXT, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        await conn.execute(
            "INSERT INTO students (key, name, class_name, school, diary_id) "
            "VALUES ('OLD', 'Solomiia', '4E', 'Szkola', 1001)"
        )
        await conn.commit()

    db = Database(db_path)
    await db.connect()

    cursor = await db.db.execute("PRAGMA table_info(students)")
    columns = {row[1] for row in await cursor.fetchall()}
    assert "active" in columns

    cursor = await db.db.execute("SELECT active FROM students WHERE key = 'OLD'")
    assert (await cursor.fetchone())[0] == 1

    await db.close()


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
