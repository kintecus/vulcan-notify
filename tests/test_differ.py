"""Tests for the change detection engine."""

from vulcan_notify.db import Database
from vulcan_notify.differ import diff_attendance, diff_exams, diff_grades, diff_homework
from vulcan_notify.models import AttendanceEntry, Exam, Grade, Homework, Student

STUDENT = Student(
    key="KEY1", name="Jan Kowalski", class_name="3A",
    school="Szkola", diary_id=1001, mailbox_key="aaa",
)


async def test_new_grade_detected(db: Database) -> None:
    await db.upsert_student(STUDENT)
    grades = [
        Grade(
            column_id=100, value="5", date="15.03.2026",
            subject="Math", column_name="Test 1", category="Biezace",
            weight=1, teacher="Nowak", changed_since_login=False,
        ),
    ]
    changes = await diff_grades(STUDENT, grades, db)

    assert len(changes) == 1
    assert changes[0].change_type == "new"
    assert changes[0].item_type == "grade"
    assert changes[0].student_name == "Jan Kowalski"
    assert "5" in changes[0].title
    assert "Math" in changes[0].title


async def test_same_grade_not_reported_twice(db: Database) -> None:
    await db.upsert_student(STUDENT)
    grade = Grade(
        column_id=100, value="5", date="15.03.2026",
        subject="Math", column_name="Test 1", category="Biezace",
        weight=1, teacher="Nowak", changed_since_login=False,
    )

    # First diff detects it as new
    changes1 = await diff_grades(STUDENT, [grade], db)
    assert len(changes1) == 1

    # Store it
    await db.upsert_grade(STUDENT.key, grade)

    # Second diff - no changes
    changes2 = await diff_grades(STUDENT, [grade], db)
    assert len(changes2) == 0


async def test_changed_grade_detected(db: Database) -> None:
    await db.upsert_student(STUDENT)
    grade_v1 = Grade(
        column_id=100, value="4", date="15.03.2026",
        subject="Math", column_name="Test 1", category="Biezace",
        weight=1, teacher="Nowak", changed_since_login=False,
    )
    await db.upsert_grade(STUDENT.key, grade_v1)

    grade_v2 = Grade(
        column_id=100, value="5", date="15.03.2026",
        subject="Math", column_name="Test 1", category="Biezace",
        weight=1, teacher="Nowak", changed_since_login=False,
    )
    changes = await diff_grades(STUDENT, [grade_v2], db)

    assert len(changes) == 1
    assert changes[0].change_type == "updated"
    assert "4" in changes[0].title
    assert "5" in changes[0].title


async def test_multiple_new_grades(db: Database) -> None:
    await db.upsert_student(STUDENT)
    grades = [
        Grade(
            column_id=100 + i, value=str(3 + i), date="15.03.2026",
            subject=subj, column_name="Test", category="Biezace",
            weight=1, teacher="Nowak", changed_since_login=False,
        )
        for i, subj in enumerate(["Math", "Physics", "History"])
    ]
    changes = await diff_grades(STUDENT, grades, db)
    assert len(changes) == 3


async def test_new_absence_detected(db: Database) -> None:
    await db.upsert_student(STUDENT)
    entries = [
        AttendanceEntry(
            lesson_number=3, category=2, date="2026-03-14",
            subject="Math", teacher="Nowak",
            time_from="09:50", time_to="10:35",
        ),
    ]
    changes = await diff_attendance(STUDENT, entries, db)

    assert len(changes) == 1
    assert "Absent" in changes[0].title
    assert changes[0].student_name == "Jan Kowalski"


async def test_present_attendance_not_reported(db: Database) -> None:
    await db.upsert_student(STUDENT)
    entries = [
        AttendanceEntry(
            lesson_number=1, category=1, date="2026-03-14",
            subject="Math", teacher="Nowak",
            time_from="08:00", time_to="08:45",
        ),
    ]
    changes = await diff_attendance(STUDENT, entries, db)
    assert len(changes) == 0


async def test_new_exam_detected(db: Database) -> None:
    await db.upsert_student(STUDENT)
    exams = [Exam(id=10001, date="2026-03-16", subject="Przyroda", type=2)]
    changes = await diff_exams(STUDENT, exams, db)

    assert len(changes) == 1
    assert "Quiz" in changes[0].title
    assert "Przyroda" in changes[0].title


async def test_known_exam_not_reported(db: Database) -> None:
    await db.upsert_student(STUDENT)
    exam = Exam(id=10001, date="2026-03-16", subject="Przyroda", type=2)
    await db.upsert_exam(STUDENT.key, exam)

    changes = await diff_exams(STUDENT, [exam], db)
    assert len(changes) == 0


async def test_new_homework_detected(db: Database) -> None:
    await db.upsert_student(STUDENT)
    hw = [Homework(id=10002, date="2026-03-16", subject="Plastyka")]
    changes = await diff_homework(STUDENT, hw, db)

    assert len(changes) == 1
    assert "Plastyka" in changes[0].title
