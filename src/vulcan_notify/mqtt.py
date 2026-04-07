"""MQTT publisher - publishes change events to Mosquitto broker."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aiomqtt

from vulcan_notify.config import settings
from vulcan_notify.models import AttendanceEntry, Exam, Grade, Homework, Message

if TYPE_CHECKING:
    from vulcan_notify.differ import Change
    from vulcan_notify.sync import FullSyncResult

logger = logging.getLogger(__name__)

# Attendance category names (category 1 = present is never published)
_ATTENDANCE_CATEGORIES = {2: "absent", 3: "late", 4: "excused"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


_TOPIC_SEGMENTS = {
    "grade": "grades",
    "attendance": "attendance",
    "exam": "exams",
    "homework": "homework",
}


def topic_for(change: Change) -> str:
    """Map a Change to its MQTT topic."""
    prefix = settings.mqtt_topic_prefix
    student = change.student_name
    segment = _TOPIC_SEGMENTS.get(change.item_type, change.item_type)

    if change.item_type == "attendance":
        return f"{prefix}/{student}/attendance/alert"

    return f"{prefix}/{student}/{segment}/{change.change_type}"


def build_payload(change: Change) -> dict[str, object]:
    """Build a structured JSON payload from a Change and its raw model."""
    raw = change.raw
    base: dict[str, object] = {
        "student": change.student_name,
        "title": change.title,
        "message": change.body,
        "timestamp": _now_iso(),
    }

    if isinstance(raw, Grade):
        base.update(
            subject=raw.subject,
            value=raw.value,
            category=raw.column_name,
            weight=raw.weight,
            teacher=raw.teacher,
            date=raw.date,
        )
        if change.old_value is not None:
            base["old_value"] = change.old_value

    elif isinstance(raw, AttendanceEntry):
        category_name = _ATTENDANCE_CATEGORIES.get(raw.category, f"category_{raw.category}")
        base.update(
            category=category_name,
            date=raw.date,
            lesson_number=raw.lesson_number,
            subject=raw.subject,
            teacher=raw.teacher,
        )

    elif isinstance(raw, Exam):
        exam_type = {1: "test", 2: "quiz"}.get(raw.type, "exam")
        base.update(
            subject=raw.subject,
            type=exam_type,
            date=raw.date,
            description=raw.description,
            teacher=raw.teacher,
        )

    elif isinstance(raw, Homework):
        base.update(
            subject=raw.subject,
            date=raw.date,
            content=raw.content,
            teacher=raw.teacher,
        )

    return base


def build_message_payload(msg: Message) -> dict[str, object]:
    """Build a structured JSON payload for a new message."""
    return {
        "title": f"Message from {msg.sender}",
        "message": f"Subject: {msg.subject}",
        "sender": msg.sender,
        "subject": msg.subject,
        "date": msg.date,
        "mailbox": msg.mailbox,
        "has_attachments": msg.has_attachments,
        "timestamp": _now_iso(),
    }


async def publish_changes(result: FullSyncResult) -> None:
    """Publish all detected changes to MQTT broker.

    Connects, publishes, and disconnects per sync cycle.
    Fails gracefully - logs warnings but never crashes the sync.
    """
    if not settings.mqtt_enabled:
        return

    changes_count = sum(
        len(sr.all_changes) for sr in result.student_results
    ) + len(result.new_messages)

    if changes_count == 0:
        return

    try:
        async with aiomqtt.Client(
            hostname=settings.mqtt_broker,
            port=settings.mqtt_port,
            username=settings.mqtt_username,
            password=settings.mqtt_password,
        ) as client:
            # Publish per-student changes
            for sr in result.student_results:
                if sr.is_first_sync:
                    continue
                for change in sr.all_changes:
                    topic = topic_for(change)
                    payload = json.dumps(build_payload(change))
                    await client.publish(topic, payload)
                    logger.debug("MQTT publish: %s", topic)

            # Publish new messages
            if not result.is_first_message_sync:
                prefix = settings.mqtt_topic_prefix
                for msg in result.new_messages:
                    topic = f"{prefix}/{msg.mailbox}/messages/new"
                    payload = json.dumps(build_message_payload(msg))
                    await client.publish(topic, payload)
                    logger.debug("MQTT publish: %s", topic)

            logger.info("MQTT: published %d change(s)", changes_count)

    except Exception:
        logger.warning(
            "MQTT publish failed (broker: %s:%d)",
            settings.mqtt_broker,
            settings.mqtt_port,
            exc_info=True,
        )
