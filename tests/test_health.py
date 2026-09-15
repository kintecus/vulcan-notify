"""Failure-path tests: a broken fetch must never look like a quiet day.

These cover the silent-failure chain that used to run all the way from a Vulcan
500 to a green dashboard tile. Each test asserts on a link in that chain.
"""

from unittest.mock import AsyncMock

import pytest

from vulcan_notify.client import VulcanFetchError
from vulcan_notify.db import Database
from vulcan_notify.models import ClassificationPeriod, DashboardData, Student
from vulcan_notify.sync import sync_all, sync_student

STUDENT = Student(
    key="KEYA",
    name="Jan",
    class_name="3A",
    school="Szkola",
    diary_id=1001,
    mailbox_key="aaa",
)

PERIOD = ClassificationPeriod(id=1, number=2, date_from="2026-02-01", date_to="2026-08-31")


def _client(**overrides: object) -> AsyncMock:
    client = AsyncMock()
    client.get_students = AsyncMock(return_value=[STUDENT])
    client.get_periods = AsyncMock(return_value=[PERIOD])
    client.get_grades_and_summaries = AsyncMock(return_value=([], []))
    client.get_attendance = AsyncMock(return_value=[])
    client.get_exams = AsyncMock(return_value=[])
    client.get_homework = AsyncMock(return_value=[])
    client.get_schedule = AsyncMock(return_value=[])
    client.get_dashboard = AsyncMock(return_value=DashboardData(unread_messages=0))
    client.get_messages = AsyncMock(return_value=[])
    client.get_message_detail = AsyncMock(return_value=None)
    client.close = AsyncMock()
    for name, value in overrides.items():
        setattr(client, name, value)
    return client


async def test_failed_section_is_recorded_and_not_marked_fresh(db: Database) -> None:
    """A failing section records 'failed' and leaves no freshness stamp behind."""
    client = _client(
        get_grades_and_summaries=AsyncMock(side_effect=VulcanFetchError("HTTP 500"))
    )
    run_id = await db.create_sync_run()

    result = await sync_student(client, db, STUDENT, run_id=run_id)

    assert "grades" in result.failed_sections
    assert result.has_failures is True

    cursor = await db.db.execute(
        "SELECT status FROM sync_sections WHERE run_id = ? AND section = 'grades'", (run_id,)
    )
    assert (await cursor.fetchone())[0] == "failed"

    # The critical assertion: a failed fetch must not advance freshness.
    assert await db.get_state(f"last_success:{STUDENT.key}:grades") is None
    # Sections that did work still stamp normally.
    assert await db.get_state(f"last_success:{STUDENT.key}:homework") is not None


async def test_successful_section_stamps_freshness(db: Database) -> None:
    run_id = await db.create_sync_run()
    await sync_student(_client(), db, STUDENT, run_id=run_id)

    assert await db.get_state(f"last_success:{STUDENT.key}:grades") is not None


async def test_partial_failure_marks_run_degraded(db: Database) -> None:
    """One broken section degrades the run instead of reporting success."""
    client = _client(get_exams=AsyncMock(side_effect=VulcanFetchError("HTTP 503")))

    await sync_all(client, db)

    run = await db.get_last_sync_run()
    assert run is not None
    assert run["status"] == "degraded"
    assert run["errors_count"] == 1
    assert "exams" in str(run["error_detail"])


async def test_clean_run_is_completed(db: Database) -> None:
    await sync_all(_client(), db)

    run = await db.get_last_sync_run()
    assert run is not None
    assert run["status"] == "completed"
    assert run["errors_count"] == 0


async def test_empty_roster_is_a_failure_not_a_quiet_day(db: Database) -> None:
    """No students means the API shape moved or the account is suspended."""
    await sync_all(_client(get_students=AsyncMock(return_value=[])), db)

    run = await db.get_last_sync_run()
    assert run is not None
    assert run["status"] == "failed"
    assert run["errors_count"] == 1


async def test_health_reports_stale_when_nothing_ever_succeeded(db: Database) -> None:
    await db.create_sync_run()
    health = await db.get_health()

    assert health["status"] == "failed"
    assert health["stale"] is True


async def test_health_is_ok_after_a_clean_sync(db: Database) -> None:
    await sync_all(_client(), db)

    health = await db.get_health()
    assert health["status"] == "ok"
    assert health["stale"] is False
    assert health["stale_sections"] == []
    assert health["sections"]["grades"]["age_seconds"] is not None


async def test_health_flags_only_the_broken_section(db: Database) -> None:
    """A partial outage names the broken part rather than going uniformly red."""
    await sync_all(_client(), db)
    # Age out just the grades stamp.
    await db.set_state(f"last_success:{STUDENT.key}:grades", "2020-01-01T00:00:00")
    await db.commit()

    health = await db.get_health()
    assert health["status"] == "stale"
    assert health["stale_sections"] == ["grades"]
    assert health["sections"]["homework"]["stale"] is False


async def test_reconcile_marks_abandoned_runs_interrupted(db: Database) -> None:
    """A sync killed mid-flight leaves a 'running' row that nothing else cleans up."""
    await db.db.execute(
        "INSERT INTO sync_runs (started_at, status) VALUES ('2020-01-01 00:00:00', 'running')"
    )
    await db.commit()

    assert await db.reconcile_stale_runs() == 1

    cursor = await db.db.execute("SELECT status FROM sync_runs WHERE id = 1")
    assert (await cursor.fetchone())[0] == "interrupted"


async def test_reconcile_leaves_a_live_run_alone(db: Database) -> None:
    await db.create_sync_run()
    assert await db.reconcile_stale_runs() == 0


@pytest.mark.parametrize("keep_days", [0, 90])
async def test_prune_respects_retention(db: Database, keep_days: int) -> None:
    await db.db.execute(
        "INSERT INTO sync_runs (started_at, status) VALUES ('2020-01-01 00:00:00', 'completed')"
    )
    await db.commit()

    deleted = await db.prune_sync_runs(keep_days=keep_days)
    assert deleted == 1
