"""Tests for the change detection engine."""

from vulcan_notify.db import Database
from vulcan_notify.differ import detect_grade_changes


class FakeGrade:
    """Minimal grade-like object for testing."""

    def __init__(self, id: str, content: str, subject: str) -> None:
        self._data = {
            "id": id,
            "content": content,
            "subject": {"name": subject},
            "teacher": {"display_name": "Test Teacher"},
            "column": {"name": "Exam"},
        }

    def model_dump(self) -> dict:
        return self._data


async def test_new_grade_detected(db: Database) -> None:
    grades = [FakeGrade("1", "5", "Math")]
    changes = await detect_grade_changes(grades, db)

    assert len(changes) == 1
    assert changes[0].item_type == "grade"
    assert "Math" in changes[0].title
    assert "5" in changes[0].title


async def test_same_grade_not_reported_twice(db: Database) -> None:
    grades = [FakeGrade("1", "5", "Math")]

    changes1 = await detect_grade_changes(grades, db)
    assert len(changes1) == 1

    changes2 = await detect_grade_changes(grades, db)
    assert len(changes2) == 0


async def test_changed_grade_detected(db: Database) -> None:
    grades_v1 = [FakeGrade("1", "4", "Math")]
    grades_v2 = [FakeGrade("1", "5", "Math")]

    await detect_grade_changes(grades_v1, db)
    changes = await detect_grade_changes(grades_v2, db)

    assert len(changes) == 1
    assert "5" in changes[0].title


async def test_multiple_grades(db: Database) -> None:
    grades = [
        FakeGrade("1", "5", "Math"),
        FakeGrade("2", "4", "Physics"),
        FakeGrade("3", "3", "History"),
    ]
    changes = await detect_grade_changes(grades, db)
    assert len(changes) == 3
