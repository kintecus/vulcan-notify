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
        Message,
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
    description TEXT,
    teacher TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    FOREIGN KEY (student_key) REFERENCES students(key)
);

CREATE TABLE IF NOT EXISTS homework (
    id INTEGER PRIMARY KEY,
    student_key TEXT NOT NULL,
    date TEXT NOT NULL,
    subject TEXT NOT NULL,
    content TEXT,
    teacher TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    FOREIGN KEY (student_key) REFERENCES students(key)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    api_global_key TEXT UNIQUE,
    sender TEXT NOT NULL,
    subject TEXT NOT NULL,
    date TEXT,
    mailbox TEXT,
    has_attachments BOOLEAN DEFAULT 0,
    is_read BOOLEAN DEFAULT 0,
    content TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'running',
    students_synced INTEGER DEFAULT 0,
    items_processed INTEGER DEFAULT 0,
    errors_count INTEGER DEFAULT 0,
    error_detail TEXT
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

    async def commit(self) -> None:
        """Commit the current transaction."""
        await self.db.commit()

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._db

    async def _migrate(self) -> None:
        """Handle schema migrations."""
        # Drop legacy hash-based tables
        for table in ("seen_items", "poll_state"):
            cursor = await self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            if await cursor.fetchone():
                logger.info("Migrating: dropping legacy %s table", table)
                await self.db.execute(f"DROP TABLE {table}")

        # Migrate exams table (add new columns)
        cursor = await self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='exams'"
        )
        if await cursor.fetchone():
            col_cursor = await self.db.execute("PRAGMA table_info(exams)")
            columns = {row[1] for row in await col_cursor.fetchall()}
            for col, col_type in [
                ("description", "TEXT"),
                ("teacher", "TEXT"),
                ("last_seen", "TIMESTAMP"),
                ("deleted_at", "TIMESTAMP"),
            ]:
                if col not in columns:
                    logger.info("Migrating: adding %s column to exams", col)
                    await self.db.execute(f"ALTER TABLE exams ADD COLUMN {col} {col_type}")

        # Migrate homework table (add new columns)
        cursor = await self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='homework'"
        )
        if await cursor.fetchone():
            col_cursor = await self.db.execute("PRAGMA table_info(homework)")
            columns = {row[1] for row in await col_cursor.fetchall()}
            for col, col_type in [
                ("content", "TEXT"),
                ("teacher", "TEXT"),
                ("last_seen", "TIMESTAMP"),
                ("deleted_at", "TIMESTAMP"),
            ]:
                if col not in columns:
                    logger.info("Migrating: adding %s column to homework", col)
                    await self.db.execute(f"ALTER TABLE homework ADD COLUMN {col} {col_type}")

        # Migrate old messages table (missing api_global_key column)
        cursor = await self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        )
        if await cursor.fetchone():
            col_cursor = await self.db.execute("PRAGMA table_info(messages)")
            columns = {row[1] for row in await col_cursor.fetchall()}
            if "api_global_key" not in columns:
                logger.info("Migrating: recreating messages table with new schema")
                await self.db.execute("DROP TABLE messages")

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
            "INSERT INTO exams (id, student_key, date, subject, type, description, teacher) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "date=excluded.date, subject=excluded.subject, type=excluded.type, "
            "description=COALESCE(excluded.description, exams.description), "
            "teacher=COALESCE(excluded.teacher, exams.teacher), "
            "last_seen=CURRENT_TIMESTAMP, deleted_at=NULL",
            (
                exam.id, student_key, exam.date, exam.subject,
                exam.type, exam.description, exam.teacher,
            ),
        )

    async def update_exam_description(
        self, exam_id: int, description: str, teacher: str | None = None
    ) -> None:
        await self.db.execute(
            "UPDATE exams SET description = ?, teacher = COALESCE(?, teacher) WHERE id = ?",
            (description, teacher, exam_id),
        )

    async def get_exam_ids_for_student(self, student_key: str) -> set[int]:
        cursor = await self.db.execute(
            "SELECT id FROM exams WHERE student_key = ?",
            (student_key,),
        )
        return {row[0] for row in await cursor.fetchall()}

    async def get_exams_missing_detail(self, student_key: str) -> set[int]:
        cursor = await self.db.execute(
            "SELECT id FROM exams WHERE student_key = ? AND description IS NULL",
            (student_key,),
        )
        return {row[0] for row in await cursor.fetchall()}

    # ── Homework ─────────────────────────────────────────────────────

    async def upsert_homework(self, student_key: str, hw: Homework) -> None:
        await self.db.execute(
            "INSERT INTO homework (id, student_key, date, subject, content, teacher) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "date=excluded.date, subject=excluded.subject, "
            "content=COALESCE(excluded.content, homework.content), "
            "teacher=COALESCE(excluded.teacher, homework.teacher), "
            "last_seen=CURRENT_TIMESTAMP, deleted_at=NULL",
            (hw.id, student_key, hw.date, hw.subject, hw.content, hw.teacher),
        )

    async def update_homework_content(
        self, homework_id: int, content: str, teacher: str | None = None
    ) -> None:
        await self.db.execute(
            "UPDATE homework SET content = ?, teacher = COALESCE(?, teacher) WHERE id = ?",
            (content, teacher, homework_id),
        )

    async def get_homework_ids_for_student(self, student_key: str) -> set[int]:
        cursor = await self.db.execute(
            "SELECT id FROM homework WHERE student_key = ?",
            (student_key,),
        )
        return {row[0] for row in await cursor.fetchall()}

    async def get_homework_missing_detail(self, student_key: str) -> set[int]:
        cursor = await self.db.execute(
            "SELECT id FROM homework WHERE student_key = ? AND content IS NULL",
            (student_key,),
        )
        return {row[0] for row in await cursor.fetchall()}

    # ── Messages ─────────────────────────────────────────────────────

    async def upsert_message(self, msg: Message) -> None:
        await self.db.execute(
            "INSERT INTO messages (id, api_global_key, sender, subject, date, "
            "mailbox, has_attachments, is_read, content) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "is_read=excluded.is_read, content=COALESCE(excluded.content, messages.content)",
            (
                msg.id,
                msg.api_global_key,
                msg.sender,
                msg.subject,
                msg.date,
                msg.mailbox,
                msg.has_attachments,
                msg.is_read,
                msg.content,
            ),
        )

    async def get_recent_messages(self, days: int = 7) -> list[dict[str, object]]:
        cursor = await self.db.execute(
            "SELECT sender, subject, date, mailbox, content "
            "FROM messages WHERE date >= date('now', ?) ORDER BY date DESC",
            (f"-{days} days",),
        )
        rows = await cursor.fetchall()
        return [
            {
                "sender": r[0],
                "subject": r[1],
                "date": r[2],
                "mailbox": r[3],
                "content": r[4],
            }
            for r in rows
        ]

    async def get_message_ids(self) -> set[int]:
        cursor = await self.db.execute("SELECT id FROM messages")
        return {row[0] for row in await cursor.fetchall()}

    async def get_message_by_id(self, message_id: int) -> dict[str, object] | None:
        cursor = await self.db.execute(
            "SELECT id, api_global_key, sender, subject, date, mailbox, "
            "has_attachments, is_read, content FROM messages WHERE id = ?",
            (message_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "api_global_key": row[1],
            "sender": row[2],
            "subject": row[3],
            "date": row[4],
            "mailbox": row[5],
            "has_attachments": row[6],
            "is_read": row[7],
            "content": row[8],
        }

    async def update_message_content(self, message_id: int, content: str) -> None:
        await self.db.execute(
            "UPDATE messages SET content = ? WHERE id = ?",
            (content, message_id),
        )

    # ── Recent changes (for AI summarization) ───────────────────────

    async def get_recent_changes(self, days: int = 1) -> dict[str, list[dict[str, object]]]:
        """Fetch recently added grades, attendance, exams, and homework.

        Returns a dict keyed by data type, each containing a list of records
        joined with the student name for context.
        """
        since = f"-{days} days"
        result: dict[str, list[dict[str, object]]] = {}

        # Grades
        cursor = await self.db.execute(
            "SELECT s.name, g.subject, g.value, g.column_name, g.category, "
            "g.weight, g.date, g.teacher "
            "FROM grades g JOIN students s ON g.student_key = s.key "
            "WHERE g.first_seen >= datetime('now', ?) "
            "ORDER BY g.first_seen DESC",
            (since,),
        )
        rows = await cursor.fetchall()
        if rows:
            result["grades"] = [
                {
                    "student": r[0],
                    "subject": r[1],
                    "value": r[2],
                    "column_name": r[3],
                    "category": r[4],
                    "weight": r[5],
                    "date": r[6],
                    "teacher": r[7],
                }
                for r in rows
            ]

        # Attendance (non-present only: category != 1)
        cursor = await self.db.execute(
            "SELECT s.name, a.subject, a.date, a.lesson_number, a.category, a.teacher "
            "FROM attendance a JOIN students s ON a.student_key = s.key "
            "WHERE a.first_seen >= datetime('now', ?) AND a.category != 1 "
            "ORDER BY a.first_seen DESC",
            (since,),
        )
        rows = await cursor.fetchall()
        if rows:
            result["attendance"] = [
                {
                    "student": r[0],
                    "subject": r[1],
                    "date": r[2],
                    "lesson_number": r[3],
                    "category": r[4],
                    "teacher": r[5],
                }
                for r in rows
            ]

        # Exams
        cursor = await self.db.execute(
            "SELECT s.name, e.subject, e.date, e.type "
            "FROM exams e JOIN students s ON e.student_key = s.key "
            "WHERE e.first_seen >= datetime('now', ?) "
            "ORDER BY e.first_seen DESC",
            (since,),
        )
        rows = await cursor.fetchall()
        if rows:
            result["exams"] = [
                {"student": r[0], "subject": r[1], "date": r[2], "type": r[3]}
                for r in rows
            ]

        # Homework
        cursor = await self.db.execute(
            "SELECT s.name, h.subject, h.date "
            "FROM homework h JOIN students s ON h.student_key = s.key "
            "WHERE h.first_seen >= datetime('now', ?) "
            "ORDER BY h.first_seen DESC",
            (since,),
        )
        rows = await cursor.fetchall()
        if rows:
            result["homework"] = [
                {"student": r[0], "subject": r[1], "date": r[2]} for r in rows
            ]

        return result

    # ── Soft deletes ─────────────────────────────────────────────────

    async def mark_missing(
        self, student_key: str, table: str, current_ids: set[int]
    ) -> int:
        """Mark items not in current_ids as soft-deleted. Returns count."""
        if table not in ("exams", "homework"):
            raise ValueError(f"Soft deletes not supported for table: {table}")
        if not current_ids:
            return 0
        placeholders = ",".join("?" for _ in current_ids)
        cursor = await self.db.execute(
            f"UPDATE {table} SET deleted_at = CURRENT_TIMESTAMP "
            f"WHERE student_key = ? AND deleted_at IS NULL AND id NOT IN ({placeholders})",
            (student_key, *current_ids),
        )
        return cursor.rowcount

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

    # ── Sync runs ─────────────────────────────────────────────────

    async def create_sync_run(self) -> int:
        cursor = await self.db.execute(
            "INSERT INTO sync_runs (started_at, status) VALUES (CURRENT_TIMESTAMP, 'running')"
        )
        await self.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def complete_sync_run(
        self,
        run_id: int,
        status: str,
        students_synced: int = 0,
        items_processed: int = 0,
        errors_count: int = 0,
        error_detail: str | None = None,
    ) -> None:
        await self.db.execute(
            "UPDATE sync_runs SET completed_at = CURRENT_TIMESTAMP, status = ?, "
            "students_synced = ?, items_processed = ?, errors_count = ?, error_detail = ? "
            "WHERE id = ?",
            (status, students_synced, items_processed, errors_count, error_detail, run_id),
        )
        await self.commit()

    async def get_last_sync_run(self) -> dict[str, object] | None:
        cursor = await self.db.execute(
            "SELECT id, started_at, completed_at, status, students_synced, "
            "items_processed, errors_count, error_detail "
            "FROM sync_runs ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "started_at": row[1],
            "completed_at": row[2],
            "status": row[3],
            "students_synced": row[4],
            "items_processed": row[5],
            "errors_count": row[6],
            "error_detail": row[7],
        }
