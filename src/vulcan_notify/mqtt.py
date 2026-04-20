"""MQTT publisher - publishes change events to Mosquitto broker."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

import aiomqtt

from vulcan_notify.config import settings
from vulcan_notify.models import AttendanceEntry, Exam, Grade, Homework, Lesson, Message
from vulcan_notify.text import strip_html

if TYPE_CHECKING:
    from vulcan_notify.db import Database
    from vulcan_notify.differ import Change
    from vulcan_notify.sync import FullSyncResult

logger = logging.getLogger(__name__)

try:
    _VERSION = version("vulcan-notify")
except PackageNotFoundError:
    _VERSION = "0.0.0"

_OFFLINE_PAYLOAD = b'{"state":"offline"}'

# Attendance category names (category 1 = present is never published)
_ATTENDANCE_CATEGORIES = {2: "absent", 3: "late", 4: "excused"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


_TOPIC_SEGMENTS = {
    "grade": "grades",
    "attendance": "attendance",
    "exam": "exams",
    "homework": "homework",
    "substitution": "substitutions",
    "cancellation": "cancellations",
    "addition": "additions",
}


def _slugify(name: str) -> str:
    """Convert a name to lowercase kebab-case for MQTT topics."""
    return name.lower().replace(" ", "-")


def topic_for(change: Change) -> str:
    """Map a Change to its MQTT topic."""
    prefix = settings.mqtt_topic_prefix
    student = _slugify(change.student_name)
    segment = _TOPIC_SEGMENTS.get(change.item_type, change.item_type)

    if change.item_type == "attendance":
        return f"{prefix}/{student}/attendance/alert"

    return f"{prefix}/{student}/{segment}/{change.change_type}"


def _display_name(full: str) -> str:
    """Map canonical student name to a short display name for push UI."""
    return settings.display_name_map.get(full, full)


def build_payload(change: Change) -> dict[str, object]:
    """Build a structured JSON payload from a Change and its raw model."""
    raw = change.raw
    base: dict[str, object] = {
        "student": _display_name(change.student_name),
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

    elif isinstance(raw, Lesson):
        base.update(
            subject=raw.subject,
            date=raw.date,
            time_from=raw.time_from,
            time_to=raw.time_to,
            original_teacher=raw.teacher,
            original_room=raw.room,
            sub_teacher=raw.sub_teacher,
            sub_room=raw.sub_room,
            sub_type=raw.sub_type,
            absence_info=raw.absence_info,
            remarks=raw.remarks,
        )

    return base


_PREVIEW_CHARS = 200


def build_message_payload(msg: Message) -> dict[str, object]:
    """Build a structured JSON payload for a new message."""
    body_text = strip_html(msg.content) if msg.content else ""
    preview = body_text[:_PREVIEW_CHARS]
    if len(body_text) > _PREVIEW_CHARS:
        preview += "…"
    message_field = f"Subject: {msg.subject}"
    if preview:
        message_field += f"\n\n{preview}"
    return {
        "title": f"Message from {msg.sender}",
        "message": message_field,
        "sender": msg.sender,
        "subject": msg.subject,
        "body": body_text,
        "date": msg.date,
        "mailbox": msg.mailbox,
        "has_attachments": msg.has_attachments,
        "timestamp": _now_iso(),
    }


async def enqueue_changes(result: FullSyncResult, db: Database) -> None:
    """Write all detected changes to the persistent MQTT outbox.

    Called during sync — guarantees no notification is lost if the broker
    is temporarily unreachable. `drain_outbox` ships them to MQTT.
    """
    if not settings.mqtt_enabled:
        return

    for sr in result.student_results:
        if sr.is_first_sync:
            continue
        for change in sr.all_changes:
            await db.enqueue_mqtt(topic_for(change), json.dumps(build_payload(change)))

    if not result.is_first_message_sync:
        prefix = settings.mqtt_topic_prefix
        for msg in result.new_messages:
            topic = f"{prefix}/{_slugify(msg.mailbox)}/messages/new"
            await db.enqueue_mqtt(topic, json.dumps(build_message_payload(msg)))

    await db.commit()


def status_topic() -> str:
    return f"{settings.mqtt_topic_prefix}/{settings.mqtt_status_suffix}"


def build_status_payload(outbox_pending: int) -> dict[str, object]:
    """Retained heartbeat payload published at the end of every sync."""
    return {
        "state": "online",
        "ts": _now_iso(),
        "outbox_pending": outbox_pending,
        "version": _VERSION,
    }


async def drain_outbox(db: Database) -> tuple[int, int]:
    """Connect, drain the outbox, publish retained heartbeat.

    Always runs when MQTT is enabled (even if the outbox is empty) so HA can
    detect silence via the retained `status` topic + LWT. Returns (ok, pending).
    """
    if not settings.mqtt_enabled:
        return 0, 0

    queue = await db.list_mqtt_outbox()
    published_ids: list[int] = []

    will = aiomqtt.Will(
        topic=status_topic(), payload=_OFFLINE_PAYLOAD, qos=1, retain=True
    )
    try:
        async with aiomqtt.Client(
            hostname=settings.mqtt_broker,
            port=settings.mqtt_port,
            username=settings.mqtt_username,
            password=settings.mqtt_password,
            will=will,
        ) as client:
            for item in queue:
                await client.publish(str(item["topic"]), str(item["payload"]))
                published_ids.append(int(item["id"]))
                logger.debug("MQTT publish: %s", item["topic"])

            pending_after = len(queue) - len(published_ids)
            await client.publish(
                status_topic(),
                json.dumps(build_status_payload(pending_after)),
                qos=1,
                retain=True,
            )
    except Exception as exc:
        failed_ids = [int(i["id"]) for i in queue if int(i["id"]) not in published_ids]
        if failed_ids:
            await db.mark_mqtt_outbox_failure(failed_ids, f"{type(exc).__name__}: {exc}")
        logger.warning(
            "MQTT publish failed after %d/%d delivered (broker: %s:%d, %d queued for retry): %s",
            len(published_ids),
            len(queue),
            settings.mqtt_broker,
            settings.mqtt_port,
            len(failed_ids),
            exc,
        )

    if published_ids:
        await db.delete_mqtt_outbox(published_ids)
        logger.info(
            "MQTT: published %d change(s), %d pending",
            len(published_ids),
            len(queue) - len(published_ids),
        )

    await db.commit()

    return len(published_ids), len(queue) - len(published_ids)


async def publish_changes(result: FullSyncResult, db: Database) -> None:
    await enqueue_changes(result, db)
    await drain_outbox(db)
