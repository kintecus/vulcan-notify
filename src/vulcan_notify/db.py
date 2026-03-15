"""SQLite storage for eduVulcan data with full normalized schema."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from pathlib import Path

    from vulcan_notify.models import (
        AttendanceEntry,
        Exam,
        Grade,
        Homework,
        Student,
    )

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    class_name TEXT NOT NULL,
    school TEXT NOT NULL,
    diary_id INTEGER NOT NULL,
    mailbox_key TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS grades (
    student_key TEXT NOT NULL,
    column_id INTEGER NOT NULL,
    value TEXT NOT NULL,
    date TEXT NOT NULL,
    subject TEXT NOT NULL,
    column_name TEXT NOT NULL,
    category TEXT NOT NULL,
    weight INTEGER DEFAULT 1,
    teacher TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (student_key, column_id),
    FOREIGN KEY (student_key) REFERENCES students(key)
);

CREATE TABLE IF NOT EXISTS attendance (
    student_key TEXT NOT NULL,
    date TEXT NOT NULL,
    lesson_number INTEGER NOT NULL,
    category INTEGER NOT NULL,
    subject TEXT NOT NULL,
    teacher TEXT,
    time_from TEXT,
    time_to TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (student_key, date, lesson_number),
    FOREIGN KEY (student_key) REFERENCES students(key)
);

CREATE TABLE IF NOT EXISTS exams (
    id INTEGER PRIMARY KEY,
    student_key TEXT NOT NULL,
    date TEXT NOT NULL,
    subject TEXT NOT NULL,
    type INTEGER NOT NULL,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_key) REFERENCES students(key)
);

CREATE TABLE IF NOT EXISTS homework (
    id INTEGER PRIMARY KEY,
    student_key TEXT NOT NULL,
    date TEXT NOT NULL,
    subject TEXT NOT NULL,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_key) REFERENCES students(key)
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    student_key TEXT,
    sender TEXT NOT NULL,
    subject TEXT NOT NULL,
    date TEXT,
    content TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        await self._migrate()
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        logger.info("Database initialized at %s", self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._db

    async def _migrate(self) -> None:
        """Drop legacy tables from the old hash-based schema."""
        cursor = await self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='seen_items'"
        )
        if await cursor.fetchone():
            logger.info("Migrating: dropping legacy seen_items table")
            await self.db.execute("DROP TABLE seen_items")
        cursor = await self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='poll_state'"
        )
        if await cursor.fetchone():
            logger.info("Migrating: dropping legacy poll_state table")
            await self.db.execute("DROP TABLE poll_state")
        await self.db.commit()

    # ── Students ─────────────────────────────────────────────────────

    async def upsert_student(self, student: Student) -> None:
        await self.db.execute(
            "INSERT INTO students (key, name, class_name, school, diary_id, mailbox_key) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "name=excluded.name, class_name=excluded.class_name, school=excluded.school, "
            "diary_id=excluded.diary_id, mailbox_key=excluded.mailbox_key, "
            "updated_at=CURRENT_TIMESTAMP",
            (
                student.key,
                student.name,
                student.class_name,
                student.school,
                student.diary_id,
                student.mailbox_key,
            ),
        )
        await self.db.commit()

    # ── Grades ───────────────────────────────────────────────────────

    async def upsert_grade(self, student_key: str, grade: Grade) -> None:
        await self.db.execute(
            "INSERT INTO grades (student_key, column_id, value, date, subject, "
            "column_name, category, weight, teacher) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(student_key, column_id) DO UPDATE SET "
            "value=excluded.value, date=excluded.date, subject=excluded.subject, "
            "column_name=excluded.column_name, category=excluded.category, "
            "weight=excluded.weight, teacher=excluded.teacher, "
            "last_seen=CURRENT_TIMESTAMP",
            (
                student_key,
                grade.column_id,
                grade.value,
                grade.date,
                grade.subject,
                grade.column_name,
                grade.category,
                grade.weight,
                grade.teacher,
            ),
        )
        await self.db.commit()

    async def get_grades_for_student(self, student_key: str) -> list[dict[str, object]]:
        cursor = await self.db.execute(
            "SELECT column_id, value, date, subject, column_name, category, weight, teacher "
            "FROM grades WHERE student_key = ?",
            (student_key,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "column_id": r[0],
                "value": r[1],
                "date": r[2],
                "subject": r[3],
                "column_name": r[4],
                "category": r[5],
                "weight": r[6],
                "teacher": r[7],
            }
            for r in rows
        ]

    # ── Attendance ───────────────────────────────────────────────────

    async def upsert_attendance(self, student_key: str, entry: AttendanceEntry) -> None:
        await self.db.execute(
            "INSERT INTO attendance (student_key, date, lesson_number, category, "
            "subject, teacher, time_from, time_to) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(student_key, date, lesson_number) DO UPDATE SET "
            "category=excluded.category, subject=excluded.subject, "
            "teacher=excluded.teacher, time_from=excluded.time_from, "
            "time_to=excluded.time_to",
            (
                student_key,
                entry.date,
                entry.lesson_number,
                entry.category,
                entry.subject,
                entry.teacher,
                entry.time_from,
                entry.time_to,
            ),
        )
        await self.db.commit()

    async def get_attendance_for_student(self, student_key: str) -> list[dict[str, object]]:
        cursor = await self.db.execute(
            "SELECT date, lesson_number, category, subject, teacher, time_from, time_to "
            "FROM attendance WHERE student_key = ?",
            (student_key,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "date": r[0],
                "lesson_number": r[1],
                "category": r[2],
                "subject": r[3],
                "teacher": r[4],
                "time_from": r[5],
                "time_to": r[6],
            }
            for r in rows
        ]

    # ── Exams ────────────────────────────────────────────────────────

    async def upsert_exam(self, student_key: str, exam: Exam) -> None:
        await self.db.execute(
            "INSERT INTO exams (id, student_key, date, subject, type) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "date=excluded.date, subject=excluded.subject, type=excluded.type",
            (exam.id, student_key, exam.date, exam.subject, exam.type),
        )
        await self.db.commit()

    async def get_exam_ids_for_student(self, student_key: str) -> set[int]:
        cursor = await self.db.execute(
            "SELECT id FROM exams WHERE student_key = ?",
            (student_key,),
        )
        return {row[0] for row in await cursor.fetchall()}

    # ── Homework ─────────────────────────────────────────────────────

    async def upsert_homework(self, student_key: str, hw: Homework) -> None:
        await self.db.execute(
            "INSERT INTO homework (id, student_key, date, subject) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "date=excluded.date, subject=excluded.subject",
            (hw.id, student_key, hw.date, hw.subject),
        )
        await self.db.commit()

    async def get_homework_ids_for_student(self, student_key: str) -> set[int]:
        cursor = await self.db.execute(
            "SELECT id FROM homework WHERE student_key = ?",
            (student_key,),
        )
        return {row[0] for row in await cursor.fetchall()}

    # ── Sync state ───────────────────────────────────────────────────

    async def get_state(self, key: str) -> str | None:
        cursor = await self.db.execute("SELECT value FROM sync_state WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row[0] if row else None

    async def set_state(self, key: str, value: str) -> None:
        await self.db.execute(
            "INSERT INTO sync_state (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
            (key, value),
        )
        await self.db.commit()
