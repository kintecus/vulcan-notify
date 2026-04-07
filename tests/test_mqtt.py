"""Tests for the MQTT publisher module."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from vulcan_notify.differ import Change
from vulcan_notify.models import AttendanceEntry, Exam, Grade, Homework, Message, Student
from vulcan_notify.mqtt import build_message_payload, build_payload, publish_changes, topic_for
from vulcan_notify.sync import FullSyncResult, SyncResult

STUDENT = Student(
    key="KEY1",
    name="Kacper",
    class_name="3A",
    school="Szkola",
    diary_id=1001,
    mailbox_key="aaa",
)

GRADE = Grade(
    column_id=100,
    value="4+",
    date="07.04.2026",
    subject="Matematyka",
    column_name="Sprawdzian",
    category="Biezace",
    weight=3,
    teacher="Kowalska A.",
    changed_since_login=False,
)

ATTENDANCE = AttendanceEntry(
    lesson_number=3,
    category=2,
    date="2026-04-07",
    subject="Fizyka",
    teacher="Nowak B.",
    time_from="10:00",
    time_to="10:45",
)

EXAM = Exam(
    id=200,
    date="2026-04-15",
    subject="Historia",
    type=1,
    description="Rozdzial 5",
    teacher="Wisniewska C.",
)

HOMEWORK = Homework(
    id=300,
    date="2026-04-10",
    subject="Chemia",
    content="Zadania 1-5 str. 42",
    teacher="Zielinski D.",
)


def _make_change(item_type: str, raw: object, change_type: str = "new", **kwargs) -> Change:
    return Change(
        change_type=change_type,
        item_type=item_type,
        student_name="Kacper",
        title="test",
        body="test",
        raw=raw,
        **kwargs,
    )


# ── topic_for ────────────────────────────────────────────────────


def test_topic_for_new_grade() -> None:
    change = _make_change("grade", GRADE)
    assert topic_for(change) == "school/Kacper/grades/new"


def test_topic_for_updated_grade() -> None:
    change = _make_change("grade", GRADE, change_type="updated")
    assert topic_for(change) == "school/Kacper/grades/updated"


def test_topic_for_attendance() -> None:
    change = _make_change("attendance", ATTENDANCE)
    assert topic_for(change) == "school/Kacper/attendance/alert"


def test_topic_for_exam() -> None:
    change = _make_change("exam", EXAM)
    assert topic_for(change) == "school/Kacper/exams/new"


def test_topic_for_homework() -> None:
    change = _make_change("homework", HOMEWORK)
    assert topic_for(change) == "school/Kacper/homework/new"


# ── build_payload ────────────────────────────────────────────────


def test_build_payload_new_grade() -> None:
    change = _make_change("grade", GRADE)
    payload = build_payload(change)

    assert payload["student"] == "Kacper"
    assert payload["title"] == "test"
    assert payload["message"] == "test"
    assert payload["subject"] == "Matematyka"
    assert payload["value"] == "4+"
    assert payload["category"] == "Sprawdzian"
    assert payload["weight"] == 3
    assert payload["teacher"] == "Kowalska A."
    assert payload["date"] == "07.04.2026"
    assert "timestamp" in payload
    assert "old_value" not in payload


def test_build_payload_updated_grade() -> None:
    change = _make_change("grade", GRADE, change_type="updated", old_value="3")
    payload = build_payload(change)

    assert payload["value"] == "4+"
    assert payload["old_value"] == "3"


def test_build_payload_attendance() -> None:
    change = _make_change("attendance", ATTENDANCE)
    payload = build_payload(change)

    assert payload["student"] == "Kacper"
    assert payload["category"] == "absent"
    assert payload["date"] == "2026-04-07"
    assert payload["lesson_number"] == 3
    assert payload["subject"] == "Fizyka"
    assert payload["teacher"] == "Nowak B."


def test_build_payload_exam() -> None:
    change = _make_change("exam", EXAM)
    payload = build_payload(change)

    assert payload["student"] == "Kacper"
    assert payload["subject"] == "Historia"
    assert payload["type"] == "test"
    assert payload["date"] == "2026-04-15"
    assert payload["description"] == "Rozdzial 5"
    assert payload["teacher"] == "Wisniewska C."


def test_build_payload_homework() -> None:
    change = _make_change("homework", HOMEWORK)
    payload = build_payload(change)

    assert payload["student"] == "Kacper"
    assert payload["subject"] == "Chemia"
    assert payload["date"] == "2026-04-10"
    assert payload["content"] == "Zadania 1-5 str. 42"
    assert payload["teacher"] == "Zielinski D."


def test_build_payload_no_raw_still_has_title_message() -> None:
    change = Change(
        change_type="new",
        item_type="grade",
        student_name="Kacper",
        title="Test title",
        body="Test body",
    )
    payload = build_payload(change)

    assert payload["title"] == "Test title"
    assert payload["message"] == "Test body"


# ── build_message_payload ────────────────────────────────────────


def test_build_message_payload() -> None:
    msg = Message(
        id=500,
        api_global_key="abc-123",
        sender="Kowalska A.",
        subject="Informacja",
        date="2026-04-07",
        mailbox="Kacper",
        has_attachments=False,
        is_read=False,
    )
    payload = build_message_payload(msg)

    assert payload["title"] == "Message from Kowalska A."
    assert payload["message"] == "Subject: Informacja"
    assert payload["sender"] == "Kowalska A."
    assert payload["subject"] == "Informacja"
    assert payload["date"] == "2026-04-07"
    assert payload["mailbox"] == "Kacper"
    assert payload["has_attachments"] is False
    assert "timestamp" in payload


# ── publish_changes ──────────────────────────────────────────────


async def test_publish_skipped_when_disabled() -> None:
    result = FullSyncResult(student_results=[])
    with patch("vulcan_notify.mqtt.settings") as mock_settings:
        mock_settings.mqtt_enabled = False
        with patch("vulcan_notify.mqtt.aiomqtt.Client") as mock_client:
            await publish_changes(result)
            mock_client.assert_not_called()


async def test_publish_skipped_when_no_changes() -> None:
    result = FullSyncResult(
        student_results=[SyncResult(student=STUDENT)],
    )
    with patch("vulcan_notify.mqtt.settings") as mock_settings:
        mock_settings.mqtt_enabled = True
        with patch("vulcan_notify.mqtt.aiomqtt.Client") as mock_client:
            await publish_changes(result)
            mock_client.assert_not_called()


async def test_publish_sends_changes() -> None:
    grade_change = _make_change("grade", GRADE)
    sr = SyncResult(student=STUDENT, new_grades=[grade_change])
    result = FullSyncResult(student_results=[sr])

    mock_client_instance = AsyncMock()
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("vulcan_notify.mqtt.settings") as mock_settings:
        mock_settings.mqtt_enabled = True
        mock_settings.mqtt_broker = "localhost"
        mock_settings.mqtt_port = 1883
        mock_settings.mqtt_username = None
        mock_settings.mqtt_password = None
        mock_settings.mqtt_topic_prefix = "school"
        with patch("vulcan_notify.mqtt.aiomqtt.Client", return_value=mock_client_ctx):
            await publish_changes(result)

    mock_client_instance.publish.assert_called_once()
    call_args = mock_client_instance.publish.call_args
    assert call_args[0][0] == "school/Kacper/grades/new"


async def test_publish_skips_first_sync() -> None:
    grade_change = _make_change("grade", GRADE)
    sr = SyncResult(student=STUDENT, new_grades=[grade_change], is_first_sync=True)
    result = FullSyncResult(student_results=[sr])

    mock_client_instance = AsyncMock()
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("vulcan_notify.mqtt.settings") as mock_settings:
        mock_settings.mqtt_enabled = True
        mock_settings.mqtt_broker = "localhost"
        mock_settings.mqtt_port = 1883
        mock_settings.mqtt_username = None
        mock_settings.mqtt_password = None
        mock_settings.mqtt_topic_prefix = "school"
        with patch("vulcan_notify.mqtt.aiomqtt.Client", return_value=mock_client_ctx):
            await publish_changes(result)

    mock_client_instance.publish.assert_not_called()


async def test_publish_graceful_on_connection_failure() -> None:
    grade_change = _make_change("grade", GRADE)
    sr = SyncResult(student=STUDENT, new_grades=[grade_change])
    result = FullSyncResult(student_results=[sr])

    with patch("vulcan_notify.mqtt.settings") as mock_settings:
        mock_settings.mqtt_enabled = True
        mock_settings.mqtt_broker = "unreachable"
        mock_settings.mqtt_port = 1883
        mock_settings.mqtt_username = None
        mock_settings.mqtt_password = None
        mock_settings.mqtt_topic_prefix = "school"
        with patch("vulcan_notify.mqtt.aiomqtt.Client", side_effect=OSError("Connection refused")):
            # Should not raise
            await publish_changes(result)
