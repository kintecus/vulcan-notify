"""Tests for the HA-facing HTTP API."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from vulcan_notify import api as api_mod
from vulcan_notify.config import settings
from vulcan_notify.db import Database
from vulcan_notify.models import Exam, Grade, Student


@pytest.fixture
async def seeded_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Seed a DB with one student and grades across Jan-Mar 2026."""
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(settings, "db_path", db_path)

    database = Database(db_path)
    await database.connect()
    await database.upsert_student(
        Student(
            key="S1",
            name="Solomiia",
            class_name="3A",
            school="Sz",
            diary_id=1,
            mailbox_key=None,
        ),
    )

    # Jan: 3 and 3 with weights 1,1 -> avg 3.0 (count 2)
    # Feb: "5+" (5.5) weight 2, "4" weight 1 -> (11+4)/3 = 5.0 (count 2)
    # Mar: "np" (non-numeric, skipped), "4-" (3.75) weight 1 -> 3.75 (count 1)
    grades = [
        ("01.01.2026", "3", 1, 101),
        ("15.01.2026", "3", 1, 102),
        ("05.02.2026", "5+", 2, 103),
        ("20.02.2026", "4", 1, 104),
        ("10.03.2026", "np", 1, 105),
        ("12.03.2026", "4-", 1, 106),
    ]
    for date, value, weight, col_id in grades:
        await database.upsert_grade(
            "S1",
            Grade(
                column_id=col_id,
                value=value,
                date=date,
                subject="Math",
                column_name="Test",
                category="1",
                weight=weight,
                teacher="T",
                changed_since_login=False,
            ),
        )
    await database.db.commit()
    await database.close()
    return db_path


async def test_monthly_averages_year_mode(seeded_db: Path) -> None:
    result = api_mod._get_monthly_averages(year=2026)
    months = result["Solomiia"]["months"]
    assert len(months) == 12
    by_key = {m["month"]: m for m in months}

    assert by_key["2026-01"]["average"] == 3.0
    assert by_key["2026-01"]["count"] == 2
    assert by_key["2026-01"]["label"] == "Jan"

    assert by_key["2026-02"]["average"] == 5.0
    assert by_key["2026-02"]["count"] == 2

    # "np" is skipped; only "4-" (3.75) counts
    assert by_key["2026-03"]["average"] == 3.75
    assert by_key["2026-03"]["count"] == 1

    # Empty months are null with count 0
    assert by_key["2026-07"]["average"] is None
    assert by_key["2026-07"]["count"] == 0


async def test_monthly_averages_default_window(seeded_db: Path) -> None:
    """Default returns last N months ending this month, ordered chronologically."""
    result = api_mod._get_monthly_averages(months=6)
    months = result["Solomiia"]["months"]
    assert len(months) == 6

    now = datetime.now()
    expected_last = f"{now.year:04d}-{now.month:02d}"
    assert months[-1]["month"] == expected_last

    # Chronological
    keys = [m["month"] for m in months]
    assert keys == sorted(keys)


async def test_monthly_averages_student_filter(seeded_db: Path) -> None:
    assert "Solomiia" in api_mod._get_monthly_averages(student_filter="Solomiia", year=2026)
    assert api_mod._get_monthly_averages(student_filter="Ghost", year=2026) == {}


async def test_subject_averages(seeded_db: Path) -> None:
    result = api_mod._get_subject_averages()
    subjects = result["Solomiia"]["subjects"]
    # seeded data uses subject "Math" for all grades; non-numeric "np" skipped
    assert len(subjects) == 1
    row = subjects[0]
    assert row["subject"] == "Math"
    assert row["count"] == 5
    # Descending sort: single subject -> trivially first
    assert all(
        subjects[i]["average"] >= subjects[i + 1]["average"] for i in range(len(subjects) - 1)
    )


@pytest.fixture
async def db_with_improvements(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """DB with two semesters and an improvement grade — exercises period filter + dedup."""
    db_path = tmp_path / "improve.db"
    monkeypatch.setattr(settings, "db_path", db_path)

    database = Database(db_path)
    await database.connect()
    await database.upsert_student(
        Student(
            key="S1", name="Solomiia", class_name="4E", school="Sz",
            diary_id=1, mailbox_key=None,
        ),
    )
    await database.upsert_classification_period("S1", 100, 1, "2025-09-01", "2026-01-31")
    await database.upsert_classification_period("S1", 200, 2, "2026-02-01", "2026-06-30")

    # Okres 1: two grades for Math (3, 2) -> 2.5
    # Okres 2: one improvement scenario — column 201 has original (3+) marked
    #   superseded_by_grade_id=999, current improvement (5-) goes through.
    grades = [
        # Okres 1
        Grade(column_id=101, value="3", date="15.10.2025", subject="Math",
              column_name="kartkówka", category="Kartkówka", weight=1, teacher="T",
              changed_since_login=False, period_id=100, superseded_by_grade_id=None),
        Grade(column_id=102, value="2", date="20.12.2025", subject="Math",
              column_name="sprawdzian", category="Sprawdzian", weight=1, teacher="T",
              changed_since_login=False, period_id=100, superseded_by_grade_id=None),
        # Okres 2: only the improvement (5-) lands in DB; original (3+) is what
        # sync drops because the PK conflict prefers the row WITHOUT superseded.
        # We still seed both to verify the API-side guard.
        Grade(column_id=202, value="5-", date="15.04.2026", subject="Math",
              column_name="sprawdzian poprawa", category="Sprawdzian", weight=1, teacher="T",
              changed_since_login=False, period_id=200, superseded_by_grade_id=None),
        # Simulate a stale 'original' row that somehow survived — should be
        # excluded by the superseded guard, not double-counted with the improvement.
        Grade(column_id=203, value="3+", date="01.04.2026", subject="Math",
              column_name="sprawdzian (original)", category="Sprawdzian", weight=1, teacher="T",
              changed_since_login=False, period_id=200, superseded_by_grade_id=999),
        # Plus a clean Okres 2 row
        Grade(column_id=204, value="4", date="20.04.2026", subject="Math",
              column_name="kartkówka", category="Kartkówka", weight=1, teacher="T",
              changed_since_login=False, period_id=200, superseded_by_grade_id=None),
    ]
    for g in grades:
        await database.upsert_grade("S1", g)
    await database.db.commit()
    await database.close()
    return db_path


async def test_subject_averages_filters_by_current_period(
    db_with_improvements: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default period resolution picks the period covering today; older grades are excluded."""
    # Force "today" to fall inside Okres 2's date range.
    fixed_now = datetime(2026, 4, 1)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return fixed_now

    monkeypatch.setattr(api_mod, "datetime", _FixedDatetime)

    result = api_mod._get_subject_averages()
    subjects = result["Solomiia"]["subjects"]
    assert len(subjects) == 1
    # Okres 2 only: 5- (4.75) + 4 = 8.75 / 2 = 4.375 -> rounded 4.38
    # The superseded (3+) row is excluded.
    assert subjects[0]["average"] == 4.38
    assert subjects[0]["count"] == 2


async def test_subject_averages_explicit_period_request(db_with_improvements: Path) -> None:
    """period='1' selects Okres 1 by number."""
    result = api_mod._get_subject_averages(period_request="1")
    # Okres 1: 3 + 2 = 5/2 = 2.5
    assert result["Solomiia"]["subjects"][0]["average"] == 2.5
    assert result["Solomiia"]["subjects"][0]["count"] == 2


async def test_subject_averages_period_all(db_with_improvements: Path) -> None:
    """period='all' disables period filter but still excludes superseded rows."""
    result = api_mod._get_subject_averages(period_request="all")
    # All Okres 1 + all non-superseded Okres 2: 3 + 2 + 4.75 + 4 = 13.75 / 4 = 3.4375 -> 3.44
    assert result["Solomiia"]["subjects"][0]["average"] == 3.44
    assert result["Solomiia"]["subjects"][0]["count"] == 4


async def test_monthly_excludes_superseded(db_with_improvements: Path) -> None:
    """Monthly aggregation should drop superseded grades."""
    result = api_mod._get_monthly_averages(year=2026)
    by_key = {m["month"]: m for m in result["Solomiia"]["months"]}
    # April 2026 has 5- (4.75) and the superseded 3+ — only 5- should count
    # plus the clean 4 also on 20.04.2026
    assert by_key["2026-04"]["count"] == 2
    assert by_key["2026-04"]["average"] == round((4.75 + 4) / 2, 2)


async def test_grade_to_numeric_logs_unparseable(caplog: pytest.LogCaptureFixture) -> None:
    """Malformed grade strings should produce a warning (caught silent data loss)."""
    import logging

    with caplog.at_level(logging.WARNING, logger="vulcan_notify.api"):
        assert api_mod._grade_to_numeric("(3)4") is None
        assert api_mod._grade_to_numeric("+") is None
        # nc is expected and shouldn't warn
        assert api_mod._grade_to_numeric("nc") is None
        # diagnostic also shouldn't warn (handled by _is_diagnostic separately)
        assert api_mod._grade_to_numeric("86 (%)") is None

    msgs = [r.getMessage() for r in caplog.records]
    assert any("(3)4" in m for m in msgs)
    assert any("+" in m for m in msgs)
    assert not any("nc" in m for m in msgs)
    assert not any("86 (%)" in m for m in msgs)


@pytest.fixture
async def seeded_exams_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Seed a DB with two students and a mix of past/future exams."""
    db_path = tmp_path / "exams.db"
    monkeypatch.setattr(settings, "db_path", db_path)

    database = Database(db_path)
    await database.connect()
    for key, name in (("S1", "Yarema"), ("S2", "Solomiia")):
        await database.upsert_student(
            Student(
                key=key, name=name, class_name="3A", school="Sz",
                diary_id=1, mailbox_key=None,
            ),
        )

    today = datetime.now()
    past = (today - timedelta(days=5)).strftime("%Y-%m-%dT00:00:00+02:00")
    near = (today + timedelta(days=3)).strftime("%Y-%m-%dT00:00:00+02:00")
    far = (today + timedelta(days=40)).strftime("%Y-%m-%dT00:00:00+02:00")

    exams = [
        ("S1", 1, past, "Matematyka", 1, "Past test", "T1"),
        ("S1", 2, near, "Polski", 2, "Quiz near", "T2"),
        ("S1", 3, far, "Historia", 1, "Far test", "T3"),
        ("S2", 4, near, "Biologia", 1, "Solomiia near", "T4"),
    ]
    for key, eid, date, subject, etype, desc, teacher in exams:
        await database.upsert_exam(
            key,
            Exam(id=eid, date=date, subject=subject, type=etype,
                 description=desc, teacher=teacher),
        )
    await database.db.commit()
    await database.close()
    return db_path


async def test_exams_window_and_mapping(seeded_exams_db: Path) -> None:
    result = api_mod._get_exams(days_ahead=21)

    # Past exam excluded, far-future (>21 days) excluded, near included
    yarema = result["Yarema"]["exams"]
    assert len(yarema) == 1
    assert yarema[0]["subject"] == "Polski"
    assert yarema[0]["type"] == "quiz"
    assert len(yarema[0]["date"]) == 10  # trimmed to YYYY-MM-DD

    assert result["Yarema"]["count"] == 1
    assert result["Solomiia"]["count"] == 1


async def test_exams_student_filter(seeded_exams_db: Path) -> None:
    result = api_mod._get_exams(student_filter="Yarema", days_ahead=21)
    assert list(result.keys()) == ["Yarema"]
    assert api_mod._get_exams(student_filter="Ghost") == {}


async def test_exams_days_ahead_extends_window(seeded_exams_db: Path) -> None:
    result = api_mod._get_exams(student_filter="Yarema", days_ahead=60)
    subjects = [e["subject"] for e in result["Yarema"]["exams"]]
    # Near (3d) + far (40d) both fit in 60d window
    assert subjects == ["Polski", "Historia"]


def test_grade_to_numeric_rejects_percent_diagnostics() -> None:
    """Diagnoza results stored as '35 (%)' must not parse to 3.0."""
    assert api_mod._grade_to_numeric("35 (%)") is None
    assert api_mod._grade_to_numeric("86 (%)") is None
    assert api_mod._grade_to_numeric("100%") is None
    # Regular grades still parse correctly
    assert api_mod._grade_to_numeric("3") == 3.0
    assert api_mod._grade_to_numeric("5+") == 5.5
    assert api_mod._grade_to_numeric("4-") == 3.75
    assert api_mod._grade_to_numeric("6p") == 6.0
    assert api_mod._grade_to_numeric("nb") is None


@pytest.fixture
async def seeded_diag_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Seed a DB with mix of regular grades and percent-style diagnostics."""
    db_path = tmp_path / "diag.db"
    monkeypatch.setattr(settings, "db_path", db_path)
    database = Database(db_path)
    await database.connect()
    await database.upsert_student(
        Student(key="S1", name="Solomiia", class_name="4E", school="Sz",
                diary_id=1, mailbox_key=None),
    )
    today = datetime.now().strftime("%d.%m.%Y")
    long_ago = (datetime.now() - timedelta(days=400)).strftime("%d.%m.%Y")
    grades = [
        (today, "4", 1, 201, "Matematyka"),
        (today, "5", 1, 202, "Matematyka"),
        (today, "35 (%)", 1, 203, "Matematyka"),
        (long_ago, "80 (%)", 1, 204, "Matematyka"),
    ]
    for date, value, weight, col_id, subj in grades:
        await database.upsert_grade(
            "S1",
            Grade(column_id=col_id, value=value, date=date, subject=subj,
                  column_name="t", category="c", weight=weight, teacher="T",
                  changed_since_login=False),
        )
    await database.db.commit()
    await database.close()
    return db_path


async def test_grades_separates_diagnostics(seeded_diag_db: Path) -> None:
    result = api_mod._get_grades(n=10)
    sol = result["Solomiia"]
    # Regular grades exclude any '%' value
    assert all("%" not in g["value"] for g in sol["grades"])
    assert len(sol["grades"]) == 2
    # Recent diagnostic surfaced; long-ago one filtered by diagnostic_days window
    assert len(sol["diagnostics"]) == 1
    assert sol["diagnostics"][0]["value"] == "35 (%)"


async def test_subject_averages_skips_diagnostics(seeded_diag_db: Path) -> None:
    """A 35% diagnostic must not be parsed as grade 3."""
    result = api_mod._get_subject_averages()
    subjects = result["Solomiia"]["subjects"]
    math = next(s for s in subjects if s["subject"] == "Matematyka")
    # Only the 4 and 5 count -> avg 4.5, count 2
    assert math["count"] == 2
    assert math["average"] == 4.5


def test_month_list_year_mode() -> None:
    out = api_mod._month_list(2025, months=6)
    assert out == [f"2025-{m:02d}" for m in range(1, 13)]


def test_month_list_relative_mode() -> None:
    out = api_mod._month_list(None, months=3)
    assert len(out) == 3
    assert out == sorted(out)
