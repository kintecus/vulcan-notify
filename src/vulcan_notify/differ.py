"""Change detection and diff engine for Vulcan API data."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from vulcan_notify.db import Database  # noqa: TC001

logger = logging.getLogger(__name__)


@dataclass
class Change:
    """Represents a detected change in Vulcan data."""

    item_type: str  # "grade", "message", "attendance", "announcement"
    item_id: str
    title: str
    body: str
    priority: int = 3  # ntfy priority 1-5
    tags: list[str] | None = None


def _hash_dict(data: dict[str, Any]) -> str:
    """Create a stable hash of a dictionary for change detection."""
    # Sort keys for deterministic hashing
    import json

    serialized = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


async def detect_grade_changes(
    grades: list[Any],
    db: Database,
) -> list[Change]:
    """Detect new or changed grades."""
    changes: list[Change] = []

    for grade in grades:
        grade_data = grade.model_dump() if hasattr(grade, "model_dump") else dict(grade)
        grade_id = str(grade_data.get("id", grade_data.get("key", "")))
        grade_hash = _hash_dict(grade_data)

        if await db.is_new_or_changed("grade", grade_id, grade_hash):
            subject = grade_data.get("subject", {})
            subject_name = (
                subject.get("name", "Unknown") if isinstance(subject, dict) else str(subject)
            )
            content = grade_data.get("content", grade_data.get("entry", ""))
            teacher = grade_data.get("teacher", {})
            teacher_name = (
                teacher.get("display_name", "") if isinstance(teacher, dict) else str(teacher)
            )
            column = grade_data.get("column", {})
            category = column.get("name", "") if isinstance(column, dict) else ""

            body_parts = [f"Subject: {subject_name}", f"Grade: {content}"]
            if category:
                body_parts.append(f"Category: {category}")
            if teacher_name:
                body_parts.append(f"Teacher: {teacher_name}")

            changes.append(
                Change(
                    item_type="grade",
                    item_id=grade_id,
                    title=f"New grade: {content} in {subject_name}",
                    body="\n".join(body_parts),
                    priority=4,
                    tags=["pencil2", "school"],
                )
            )

    return changes


async def detect_message_changes(
    messages: list[Any],
    db: Database,
) -> list[Change]:
    """Detect new messages."""
    changes: list[Change] = []

    for msg in messages:
        msg_data = msg.model_dump() if hasattr(msg, "model_dump") else dict(msg)
        msg_id = str(msg_data.get("id", msg_data.get("key", "")))
        msg_hash = _hash_dict(msg_data)

        if await db.is_new_or_changed("message", msg_id, msg_hash):
            sender = msg_data.get("sender", {})
            sender_name = sender.get("name", "Unknown") if isinstance(sender, dict) else str(sender)
            subject = msg_data.get("subject", "No subject")

            changes.append(
                Change(
                    item_type="message",
                    item_id=msg_id,
                    title=f"Message from {sender_name}",
                    body=f"Subject: {subject}",
                    priority=4,
                    tags=["envelope", "school"],
                )
            )

    return changes


async def detect_attendance_changes(
    attendance: list[Any],
    db: Database,
) -> list[Change]:
    """Detect attendance changes (absences, late arrivals)."""
    changes: list[Change] = []

    for entry in attendance:
        entry_data = entry.model_dump() if hasattr(entry, "model_dump") else dict(entry)
        entry_id = str(entry_data.get("id", entry_data.get("key", "")))
        entry_hash = _hash_dict(entry_data)

        if await db.is_new_or_changed("attendance", entry_id, entry_hash):
            date = entry_data.get("date", "")
            subject = entry_data.get("subject", {})
            subject_name = subject.get("name", "") if isinstance(subject, dict) else str(subject)
            presence_type = entry_data.get("type", {})
            type_name = (
                presence_type.get("name", "Change")
                if isinstance(presence_type, dict)
                else str(presence_type)
            )

            changes.append(
                Change(
                    item_type="attendance",
                    item_id=entry_id,
                    title=f"Attendance: {type_name}",
                    body=f"Date: {date}\nSubject: {subject_name}",
                    priority=3,
                    tags=["calendar", "school"],
                )
            )

    return changes


async def detect_announcement_changes(
    announcements: list[Any],
    db: Database,
) -> list[Change]:
    """Detect new announcements."""
    changes: list[Change] = []

    for ann in announcements:
        ann_data = ann.model_dump() if hasattr(ann, "model_dump") else dict(ann)
        ann_id = str(ann_data.get("id", ann_data.get("key", "")))
        ann_hash = _hash_dict(ann_data)

        if await db.is_new_or_changed("announcement", ann_id, ann_hash):
            subject = ann_data.get("subject", "Announcement")
            content = ann_data.get("content", "")

            changes.append(
                Change(
                    item_type="announcement",
                    item_id=ann_id,
                    title=f"Announcement: {subject}",
                    body=content[:200] if content else "New announcement",
                    priority=3,
                    tags=["loudspeaker", "school"],
                )
            )

    return changes
