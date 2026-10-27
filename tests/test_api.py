"""Tests for the HA-facing HTTP API."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from aiohttp.test_utils import make_mocked_request

if TYPE_CHECKING:
    from pathlib import Path

from vulcan_notify import api as api_mod
from vulcan_notify.config import settings
from vulcan_notify.db import Database
from vulcan_notify.models import AttendanceEntry, Exam, Excuse, Grade, Student, SubjectSummary


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


@pytest.fixture
async def rollover_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Seed one pupil across a school-year rollover: retired OLD key, live NEW key.

    Mirrors production, where Vulcan mints a fresh key each September and all of
    last year's grades stay attached to the retired one.
    """
    db_path = tmp_path / "rollover.db"
    monkeypatch.setattr(settings, "db_path", db_path)

    database = Database(db_path)
    await database.connect()
    for key, class_name in (("OLD", "4E"), ("NEW", "5E")):
        await database.upsert_student(
            Student(key=key, name="Solomiia", class_name=class_name, school="Sz", diary_id=1,
                    mailbox_key=None),
        )
    await database.upsert_grade(
        "OLD",
        Grade(column_id=1, value="5", date="10.05.2026", subject="Math", column_name="Test",
              category="1", weight=1, teacher="T", changed_since_login=False),
    )

    today = datetime.now()
    rows = [
        ("OLD", (today - timedelta(days=10)).strftime("%Y-%m-%d")),
        ("NEW", (today + timedelta(days=3)).strftime("%Y-%m-%d")),
    ]
    for key, date in rows:
        await database.db.execute(
            "INSERT INTO schedule (student_key, date, time_from, time_to, subject, teacher) "
            "VALUES (?, ?, ?, ?, 'Math', 'T')",
            (key, date, f"{date}T08:00:00+02:00", f"{date}T08:45:00+02:00"),
        )
    await database.db.commit()
    await database.close()
    return db_path


async def test_grades_exclude_retired_school_year(rollover_db: Path) -> None:
    """Read paths follow the live key only, and don't emit the pupil twice."""
    await _retire_old(rollover_db)

    result = api_mod._get_grades()

    assert list(result) == ["Solomiia"]
    assert result["Solomiia"]["class"] == "5E"
    assert result["Solomiia"]["grades"] == []


async def test_retired_year_grades_are_still_on_disk(rollover_db: Path) -> None:
    """Retiring a key hides it from the API without destroying the history."""
    await _retire_old(rollover_db)

    db = api_mod._connect()
    count = db.execute("SELECT COUNT(*) FROM grades WHERE student_key = 'OLD'").fetchone()[0]
    db.close()

    assert count == 1


async def test_ics_feed_still_spans_both_keys(rollover_db: Path) -> None:
    """The calendar window straddles September, so it must ignore the active flag."""
    await _retire_old(rollover_db)

    _key, lessons = api_mod._get_lessons_for_ics("Solomiia", 30, 60)

    assert {lesson["student_key"] for lesson in lessons} == {"OLD", "NEW"}


async def _retire_old(db_path: Path) -> None:
    database = Database(db_path)
    await database.connect()
    await database.deactivate_students_except({"NEW"})
    await database.commit()
    await database.close()


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


@pytest.fixture
async def db_with_summaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "summary.db"
    monkeypatch.setattr(settings, "db_path", db_path)
    database = Database(db_path)
    await database.connect()
    await database.upsert_student(
        Student(key="S1", name="Solomiia", class_name="4E", school="Sz",
                diary_id=1, mailbox_key=None),
    )
    await database.upsert_classification_period("S1", 100, 1, "2025-09-01", "2026-01-31")
    await database.upsert_classification_period("S1", 200, 2, "2026-02-01", "2026-06-30")
    # Okres 1: finals set
    for subj, final in [("Matematyka", "3"), ("Język polski", "4"), ("Plastyka", "6")]:
        await database.upsert_subject_summary(
            "S1",
            SubjectSummary(subject=subj, period_id=100, final_grade=final,
                          proposed_final_grade=None, use_weighted_average=True),
        )
    # Okres 2: finals blank, one proposed
    for subj, prop in [("Matematyka", None), ("Język polski", None), ("Plastyka", "5+")]:
        await database.upsert_subject_summary(
            "S1",
            SubjectSummary(subject=subj, period_id=200, final_grade=None,
                          proposed_final_grade=prop, use_weighted_average=True),
        )
    await database.db.commit()
    await database.close()
    return db_path


async def test_subject_summaries_okres_1(db_with_summaries: Path) -> None:
    result = api_mod._get_subject_summaries(period_request="1")
    body = result["Solomiia"]
    assert body["period"]["number"] == 1
    by_subject = {s["subject"]: s for s in body["subjects"]}
    assert by_subject["Matematyka"]["final_grade"] == "3"
    assert by_subject["Język polski"]["final_grade"] == "4"
    assert by_subject["Plastyka"]["final_grade"] == "6"
    assert all(s["proposed_final_grade"] is None for s in body["subjects"])


async def test_subject_summaries_okres_2_proposed_only(db_with_summaries: Path) -> None:
    result = api_mod._get_subject_summaries(period_request="2")
    body = result["Solomiia"]
    by_subject = {s["subject"]: s for s in body["subjects"]}
    assert all(s["final_grade"] is None for s in body["subjects"])
    assert by_subject["Plastyka"]["proposed_final_grade"] == "5+"
    assert by_subject["Matematyka"]["proposed_final_grade"] is None


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


@pytest.mark.parametrize(
    ("status", "query", "expected"),
    [
        ("ok", "", 200),
        ("degraded", "", 200),
        ("stale", "", 503),
        ("failed", "", 503),
        # ?soft=1 is the variant HA reads. Its REST sensor discards the body on
        # any non-2xx, which would blind the dashboard at exactly the moment the
        # payload starts being worth reading.
        ("stale", "soft=1", 200),
        ("failed", "soft=true", 200),
        ("stale", "soft=0", 503),
    ],
)
async def test_health_status_code(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    query: str,
    expected: int,
) -> None:
    monkeypatch.setattr(api_mod, "_get_health", lambda: {"status": status})
    path = f"/api/health?{query}" if query else "/api/health"
    response = await api_mod.handle_health(make_mocked_request("GET", path))
    assert response.status == expected
    # The body is identical either way; only the status code differs.
    assert json.loads(response.text)["status"] == status


def test_month_list_year_mode() -> None:
    out = api_mod._month_list(2025, months=6)
    assert out == [f"2025-{m:02d}" for m in range(1, 13)]


def test_month_list_relative_mode() -> None:
    out = api_mod._month_list(None, months=3)
    assert len(out) == 3
    assert out == sorted(out)


async def test_health_reports_recent_drift(seeded_db: Path) -> None:
    from vulcan_notify.drift import Drift

    db = Database(seeded_db)
    await db.connect()
    run_id = await db.create_sync_run()
    drift = Drift("uczen:Oceny", "missing", "$.x", "key no longer returned")
    await db.record_drifts([drift], run_id)
    await db.commit()
    await db.close()

    drift = api_mod._get_health()["api_drift"]
    assert drift["count"] == 1
    assert drift["recent"][0]["endpoint"] == "uczen:Oceny"


def test_health_survives_missing_drift_table(tmp_path: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(tmp_path / "bare.db")
    assert api_mod._recent_drift(conn) is None
    conn.close()


async def _log_change(
    db_path: Path, student: str | None, item_type: str, detected_at: str | None = None
) -> None:
    db = Database(db_path)
    await db.connect()
    await db.record_change(
        None, student, item_type, "new", f"{item_type} title", "body",
        json.dumps({"student": student, "title": "t", "subject": "Math", "timestamp": "x"}),
    )
    if detected_at:
        await db.db.execute(
            "UPDATE changes SET detected_at = ? WHERE id = last_insert_rowid()", (detected_at,)
        )
    await db.commit()
    await db.close()


async def test_changes_since_window_and_shape(seeded_db: Path) -> None:
    await _log_change(seeded_db, "Solomiia", "grade", "2020-01-01 00:00:00")
    await _log_change(seeded_db, "Solomiia", "exam")

    out = api_mod._get_changes("7d")
    assert out["count"] == 1
    change = out["changes"][0]
    assert change["type"] == "exam"
    assert change["detected_at"].endswith("+00:00")
    # Envelope keys duplicated by columns are dropped from details.
    assert change["details"] == {"subject": "Math"}

    assert api_mod._get_changes("2019-12-31T23:00:00+00:00")["count"] == 2


async def test_changes_filters(seeded_db: Path) -> None:
    await _log_change(seeded_db, "Solomiia", "grade")
    await _log_change(seeded_db, "Other", "grade")
    await _log_change(seeded_db, None, "message")

    by_student = api_mod._get_changes(student_filter="Solomiia")
    # Account-wide messages stay in a per-student view.
    assert sorted(c["type"] for c in by_student["changes"]) == ["grade", "message"]

    assert api_mod._get_changes(types=["message"])["count"] == 1

    limited = api_mod._get_changes(limit=2)
    assert limited["count"] == 2 and limited["truncated"] is True
    assert [c["type"] for c in limited["changes"]] == ["message", "grade"]  # newest first


async def test_changes_bad_since_is_400(seeded_db: Path) -> None:
    app = api_mod.create_app()
    handler = app.middlewares[0]
    response = await handler(
        make_mocked_request("GET", "/api/changes?since=yesterday", app=app),
        api_mod.handle_changes,
    )
    assert response.status == 400


def test_changes_before_first_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "db_path", tmp_path / "empty.db")
    assert api_mod._get_changes()["changes"] == []


async def test_attendance_hides_present_and_joins_excuses(seeded_db: Path) -> None:
    day = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    old = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    db = Database(seeded_db)
    await db.connect()
    for date, lesson, category in [(day, 1, 1), (day, 2, 2), (day, 3, 6), (old, 1, 2)]:
        await db.upsert_attendance(
            "S1",
            AttendanceEntry(
                lesson_number=lesson,
                category=category,
                date=f"{date}T00:00:00+02:00",
                subject="Math",
                teacher="T",
                time_from=f"{date}T08:{lesson}0:00+02:00",
                time_to="",
            ),
        )
    await db.upsert_excuse(
        "S1", Excuse(id=1, day_id=1, date=f"{day}T00:00:00+02:00", lesson_number=2, status=1)
    )
    await db.commit()
    await db.close()

    sol = api_mod._get_attendance(days=30)["Solomiia"]
    assert sol["summary"] == {"present": 1, "absent": 1, "category_6": 1}
    assert [(r["lesson_number"], r["category"]) for r in sol["records"]] == [
        (3, "category_6"),
        (2, "absent"),
    ]
    assert sol["records"][1]["excuse_status"] == 1
    assert sol["records"][1]["time_from"] == "08:20"
    assert sol["records"][0]["excuse_status"] is None

    assert api_mod._get_attendance(days=30, include_present=True)["Solomiia"]["count"] == 3


async def test_changes_flags_history_that_starts_inside_the_window(seeded_db: Path) -> None:
    out = api_mod._get_changes("7d")
    # The log began when the schema was created, i.e. just now: an empty list
    # here means "no history yet", not "a quiet week".
    assert out["count"] == 0
    assert out["log_started_at"] is not None
    assert out["complete"] is False

    db = Database(seeded_db)
    await db.connect()
    await db.set_state("changes_log_started", "2020-01-01 00:00:00")
    await db.commit()
    await db.close()
    assert api_mod._get_changes("7d")["complete"] is True


async def test_log_start_marker_keeps_oldest_existing_change(tmp_path: Path) -> None:
    db = Database(tmp_path / "m.db")
    await db.connect()
    await db.db.execute("DELETE FROM sync_state WHERE key = 'changes_log_started'")
    await db.record_change(None, "A", "grade", "new", "t", "b", "{}")
    await db.db.execute("UPDATE changes SET detected_at = '2026-01-02 03:04:05'")
    await db.commit()
    await db.close()

    await db.connect()  # existing install upgrading: marker backfills from the log
    assert await db.get_state("changes_log_started") == "2026-01-02 03:04:05"
    await db.close()
