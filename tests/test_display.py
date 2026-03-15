"""Tests for terminal output formatting."""

from unittest.mock import patch

from vulcan_notify.differ import Change
from vulcan_notify.display import (
    filter_messages,
    filter_messages_by_whitelist,
    format_change,
    format_full_sync,
)
from vulcan_notify.models import Message, Student
from vulcan_notify.sync import FullSyncResult, SyncResult

STUDENT = Student(
    key="KEY1",
    name="Jan Kowalski",
    class_name="3A",
    school="Szkola",
    diary_id=1001,
    mailbox_key="aaa",
)


def _grade_change(change_type: str = "new", value: str = "5") -> Change:
    title = (
        f"New grade: {value} in Math"
        if change_type == "new"
        else f"Grade changed: 4 -> {value} in Math"
    )
    return Change(
        change_type=change_type,
        item_type="grade",
        student_name="Jan Kowalski",
        title=title,
        body=f"Grade: {value}",
    )


def _attendance_change() -> Change:
    return Change(
        change_type="new",
        item_type="attendance",
        student_name="Jan Kowalski",
        title="Attendance: Absent",
        body="Date: 2026-03-14",
    )


def _make_message(
    sender: str = "Nowak Anna - P - (ZSIJP)",
    subject: str = "Test message",
    mailbox: str = "Senyuk Ostap - R - Senyuk Yarema - (ZSIJP)",
) -> Message:
    return Message(
        id=1,
        api_global_key="aaa-bbb",
        sender=sender,
        subject=subject,
        date="2026-03-15T11:00:00+01:00",
        mailbox=mailbox,
        has_attachments=False,
        is_read=False,
        content="<p>Hello world</p>",
    )


def test_format_new_grade() -> None:
    line = format_change(_grade_change("new"))
    assert "+" in line
    assert "New grade: 5 in Math" in line


def test_format_updated_grade() -> None:
    line = format_change(_grade_change("updated"))
    assert "~" in line
    assert "4 -> 5" in line


def test_format_attendance_has_bang() -> None:
    line = format_change(_attendance_change())
    assert "!" in line
    assert "Absent" in line


def test_format_full_sync_with_changes() -> None:
    sr = SyncResult(
        student=STUDENT,
        new_grades=[_grade_change("new")],
        new_attendance=[_attendance_change()],
    )
    result = FullSyncResult(student_results=[sr])
    output = format_full_sync(result)
    assert "Jan Kowalski (3A)" in output
    assert "Grades:" in output
    assert "Attendance:" in output
    assert "New grade: 5" in output
    assert "Absent" in output


def test_format_full_sync_no_changes() -> None:
    sr = SyncResult(student=STUDENT)
    result = FullSyncResult(student_results=[sr])
    output = format_full_sync(result)
    assert "No changes since last sync" in output


def test_format_full_sync_first_sync() -> None:
    sr = SyncResult(student=STUDENT, is_first_sync=True)
    result = FullSyncResult(student_results=[sr])
    output = format_full_sync(result)
    assert "Initial sync complete" in output


def test_format_full_sync_with_messages() -> None:
    sr = SyncResult(student=STUDENT)
    msg = _make_message()
    result = FullSyncResult(
        student_results=[sr],
        new_messages=[msg],
    )
    output = format_full_sync(result)
    assert "Messages (1 new)" in output
    assert "Nowak A." in output
    assert "Test message" in output


def test_format_full_sync_message_whitelist() -> None:
    sr = SyncResult(student=STUDENT)
    msg1 = _make_message(sender="Nowak Anna - P - (ZSIJP)", subject="Important")
    msg2 = _make_message(sender="System - P - (ZSIJP)", subject="Spam")
    result = FullSyncResult(
        student_results=[sr],
        new_messages=[msg1, msg2],
    )
    output = format_full_sync(result, whitelist=["Nowak"])
    assert "Important" in output
    assert "Spam" not in output
    assert "filtered by whitelist" in output


def test_format_full_sync_first_message_sync() -> None:
    sr = SyncResult(student=STUDENT)
    result = FullSyncResult(
        student_results=[sr],
        is_first_message_sync=True,
    )
    output = format_full_sync(result)
    assert "Messages" in output
    assert "Initial sync complete" in output


@patch("vulcan_notify.display._is_tty", False)
def test_no_ansi_when_not_tty() -> None:
    import importlib

    import vulcan_notify.display as disp

    importlib.reload(disp)
    try:
        sr = SyncResult(
            student=STUDENT,
            new_grades=[_grade_change("new")],
        )
        result = FullSyncResult(student_results=[sr])
        output = disp.format_full_sync(result)
        assert "\033[" not in output
    finally:
        importlib.reload(disp)


def test_filter_messages_empty_whitelist() -> None:
    msgs = [_make_message(sender="A"), _make_message(sender="B")]
    result = filter_messages(msgs, [])
    assert len(result) == 2


def test_filter_messages_with_whitelist() -> None:
    msgs = [
        _make_message(sender="Nowak Anna - P"),
        _make_message(sender="System"),
    ]
    result = filter_messages(msgs, ["Nowak"])
    assert len(result) == 1
    assert "Nowak" in result[0].sender


def test_filter_messages_by_whitelist_strings() -> None:
    senders = ["Nowak Anna [AN]", "Kowalski Jan [KJ]", "System"]
    result = filter_messages_by_whitelist(senders, ["Nowak"])
    assert result == ["Nowak Anna [AN]"]


def test_filter_messages_case_insensitive() -> None:
    msgs = [_make_message(sender="Nowak Anna")]
    result = filter_messages(msgs, ["nowak"])
    assert len(result) == 1


def test_filter_messages_no_match() -> None:
    msgs = [_make_message(sender="Teacher A")]
    result = filter_messages(msgs, ["Nobody"])
    assert result == []
