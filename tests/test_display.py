"""Tests for terminal output formatting."""

from unittest.mock import patch

from vulcan_notify.differ import Change
from vulcan_notify.display import (
    filter_messages_by_whitelist,
    format_change,
    format_sync_results,
)
from vulcan_notify.models import Student
from vulcan_notify.sync import SyncResult

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


def test_format_sync_results_with_changes() -> None:
    result = SyncResult(
        student=STUDENT,
        new_grades=[_grade_change("new")],
        new_attendance=[_attendance_change()],
    )
    output = format_sync_results([result])
    assert "Jan Kowalski (3A)" in output
    assert "Grades:" in output
    assert "Attendance:" in output
    assert "New grade: 5" in output
    assert "Absent" in output


def test_format_sync_results_no_changes() -> None:
    result = SyncResult(student=STUDENT)
    output = format_sync_results([result])
    assert "No changes since last sync" in output


def test_format_sync_results_first_sync() -> None:
    result = SyncResult(student=STUDENT, is_first_sync=True)
    output = format_sync_results([result])
    assert "Initial sync complete" in output


def test_format_sync_results_unread_messages() -> None:
    result = SyncResult(student=STUDENT, unread_messages=42)
    output = format_sync_results([result])
    assert "Unread messages: 42" in output


@patch("vulcan_notify.display._is_tty", False)
def test_no_ansi_when_not_tty() -> None:
    # Re-import to pick up the patched _is_tty
    import importlib

    import vulcan_notify.display as disp

    importlib.reload(disp)
    try:
        result = SyncResult(
            student=STUDENT,
            new_grades=[_grade_change("new")],
        )
        output = disp.format_sync_results([result])
        assert "\033[" not in output
    finally:
        importlib.reload(disp)


def test_filter_messages_empty_whitelist() -> None:
    senders = ["Teacher A", "Teacher B", "Admin"]
    result = filter_messages_by_whitelist(senders, [])
    assert result == senders


def test_filter_messages_with_whitelist() -> None:
    senders = ["Nowak Anna [AN]", "Kowalski Jan [KJ]", "System"]
    result = filter_messages_by_whitelist(senders, ["Nowak"])
    assert result == ["Nowak Anna [AN]"]


def test_filter_messages_case_insensitive() -> None:
    senders = ["Nowak Anna [AN]"]
    result = filter_messages_by_whitelist(senders, ["nowak"])
    assert len(result) == 1


def test_filter_messages_substring_match() -> None:
    senders = ["Dragosz Aneta [AD]"]
    result = filter_messages_by_whitelist(senders, ["Dragosz"])
    assert len(result) == 1


def test_filter_messages_no_match() -> None:
    senders = ["Teacher A", "Teacher B"]
    result = filter_messages_by_whitelist(senders, ["Nobody"])
    assert result == []
