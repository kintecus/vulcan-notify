"""Tests for API drift detection: shape extraction, reconciliation, and the sync hook."""

from unittest.mock import AsyncMock

from vulcan_notify.db import Database
from vulcan_notify.drift import (
    KnownPath,
    endpoint_name,
    extract,
    parent,
    reconcile,
    summarize,
)
from vulcan_notify.sync import detect_api_drift

GRADES = {
    "ocenyPrzedmioty": [
        {
            "przedmiotNazwa": "Matematyka",
            "kolumnyOcenyCzastkowe": [{"oceny": [{"wpis": "5", "waga": 2, "poprawa": None}]}],
        }
    ]
}


def _known(data: object) -> dict[str, KnownPath]:
    return {p: KnownPath(t) for p, t in extract(data).items()}


def test_endpoint_name_strips_tenant_and_query() -> None:
    url = "https://uczen.eduvulcan.pl/district/api/Oceny?key=ABC&idOkresKlasyfikacyjny=1"
    assert endpoint_name(url) == "uczen:Oceny"
    assert endpoint_name("https://wiadomosci.eduvulcan.pl/d/api/Odebrane?x=1") == (
        "wiadomosci:Odebrane"
    )


def test_extract_records_paths_and_types_not_values() -> None:
    shape = extract(GRADES)
    assert shape["$.ocenyPrzedmioty[].przedmiotNazwa"] == {"string"}
    assert shape["$.ocenyPrzedmioty[].kolumnyOcenyCzastkowe[].oceny[].waga"] == {"number"}
    assert shape["$.ocenyPrzedmioty[].kolumnyOcenyCzastkowe[].oceny[].poprawa"] == {"null"}
    assert "Matematyka" not in repr(shape)


def test_extract_collapses_data_keys() -> None:
    shape = extract({"oddzialy": {"123": {"a": 1}, "2026-01-01": {"a": 2}}})
    assert "$.oddzialy.*.a" in shape
    assert not any("123" in p for p in shape)


def test_parent() -> None:
    assert parent("$.a[].b") == "$.a[]"
    assert parent("$.a[]") == "$.a"
    assert parent("$.a") == "$"


def test_first_sighting_is_learned_silently() -> None:
    result = reconcile("uczen:Oceny", {}, extract(GRADES))
    assert result.drifts == []
    assert result.changed
    assert "$.ocenyPrzedmioty[].przedmiotNazwa" in result.known


def test_identical_shape_is_quiet_and_unchanged() -> None:
    result = reconcile("uczen:Oceny", _known(GRADES), extract(GRADES))
    assert result.drifts == []
    assert not result.changed


def test_renamed_key_reports_added_and_missing() -> None:
    renamed = {"ocenyPrzedmioty": [{"nazwaPrzedmiotu": "x", "kolumnyOcenyCzastkowe": []}]}
    result = reconcile("uczen:Oceny", _known(GRADES), extract(renamed))
    kinds = {(d.kind, d.path) for d in result.drifts}
    assert ("added", "$.ocenyPrzedmioty[].nazwaPrzedmiotu") in kinds
    assert ("missing", "$.ocenyPrzedmioty[].przedmiotNazwa") in kinds


def test_empty_array_is_not_missing() -> None:
    empty = {"ocenyPrzedmioty": []}
    result = reconcile("uczen:Oceny", _known(GRADES), extract(empty))
    assert result.drifts == []


def test_missing_reported_once_then_cleared_on_return() -> None:
    renamed = {"ocenyPrzedmioty": [{"kolumnyOcenyCzastkowe": []}]}
    first = reconcile("uczen:Oceny", _known(GRADES), extract(renamed))
    assert [d.kind for d in first.drifts] == ["missing"]

    second = reconcile("uczen:Oceny", first.known, extract(renamed))
    assert second.drifts == []

    back = reconcile("uczen:Oceny", second.known, extract(GRADES))
    assert back.drifts == []
    assert not back.known["$.ocenyPrzedmioty[].przedmiotNazwa"].missing


def test_children_of_previously_null_parent_are_not_drift() -> None:
    before = {"blokada": None}
    after = {"blokada": {"dataOd": None, "oddzialy": {}}}
    result = reconcile("uczen:X", _known(before), extract(after))
    assert result.drifts == []
    assert "$.blokada.dataOd" in result.known


def test_type_change_reported() -> None:
    before = {"items": [{"id": 1}]}
    after = {"items": [{"id": "1"}]}
    result = reconcile("uczen:X", _known(before), extract(after))
    assert [(d.kind, d.path) for d in result.drifts] == [("type_changed", "$.items[].id")]


def test_nullable_field_going_null_is_quiet() -> None:
    before = {"items": [{"x": 1}, {"x": None}]}
    after = {"items": [{"x": None}]}
    assert reconcile("uczen:X", _known(before), extract(after)).drifts == []


def test_never_null_field_going_all_null_is_reported() -> None:
    before = {"items": [{"date": "2026-01-01"}]}
    after = {"items": [{"date": None}, {"date": None}]}
    result = reconcile("uczen:X", _known(before), extract(after))
    assert [(d.kind, d.path) for d in result.drifts] == [("now_null", "$.items[].date")]


def test_summarize_truncates() -> None:
    from vulcan_notify.drift import Drift

    drifts = [Drift("uczen:X", "added", f"$.k{i}", "string") for i in range(5)]
    text = summarize(drifts)
    assert text.count("\n") == 3
    assert text.endswith("+2 more")


async def test_detect_api_drift_persists_and_reports(db: Database) -> None:
    client = AsyncMock()
    client.shapes = {"uczen:Oceny": extract(GRADES)}
    run_id = await db.create_sync_run()
    assert await detect_api_drift(client, db, run_id) == []

    renamed = {"ocenyPrzedmioty": [{"nazwaPrzedmiotu": "x", "kolumnyOcenyCzastkowe": []}]}
    client.shapes = {"uczen:Oceny": extract(renamed)}
    run_id = await db.create_sync_run()
    drifts = await detect_api_drift(client, db, run_id)
    assert {d.kind for d in drifts} == {"added", "missing"}

    cursor = await db.db.execute("SELECT kind FROM api_drift WHERE run_id = ?", (run_id,))
    assert sorted(r[0] for r in await cursor.fetchall()) == ["added", "missing"]

    # Learned: the same renamed shape is quiet next time.
    run_id = await db.create_sync_run()
    assert await detect_api_drift(client, db, run_id) == []


async def test_section_dropping_to_zero_is_flagged(db: Database) -> None:
    client = AsyncMock()
    client.shapes = {}
    for count in (12, 12, 13):
        run_id = await db.create_sync_run()
        await db.record_section(run_id, "grades", "ok", student_key="K", item_count=count)
    run_id = await db.create_sync_run()
    await db.record_section(run_id, "grades", "ok", student_key="K", item_count=0)

    drifts = await detect_api_drift(client, db, run_id)
    assert [(d.endpoint, d.kind) for d in drifts] == [("grades", "empty")]


async def test_exams_dropping_to_zero_is_normal(db: Database) -> None:
    client = AsyncMock()
    client.shapes = {}
    for count in (3, 3, 3, 0):
        run_id = await db.create_sync_run()
        await db.record_section(run_id, "exams", "ok", student_key="K", item_count=count)
    assert await detect_api_drift(client, db, run_id) == []


async def test_schedule_empty_over_a_break_is_normal(db: Database) -> None:
    from datetime import datetime

    from vulcan_notify.models import DayOff

    today = datetime.now().strftime("%Y-%m-%d")
    await db.upsert_day_off("K", DayOff("Ferie", f"{today}T00:00:00", f"{today}T23:59:59"))
    client = AsyncMock()
    client.shapes = {}
    for count in (30, 30, 30, 0):
        run_id = await db.create_sync_run()
        await db.record_section(run_id, "schedule", "ok", student_key="K", item_count=count)
    assert await detect_api_drift(client, db, run_id) == []


async def test_detect_api_drift_never_raises(db: Database) -> None:
    client = AsyncMock()
    client.shapes = None  # broken input
    run_id = await db.create_sync_run()
    assert await detect_api_drift(client, db, run_id) == []


def test_synthesize_round_trips_shape() -> None:
    from vulcan_notify.drift import synthesize

    sample = synthesize(extract(GRADES))
    assert extract(sample).keys() == extract(GRADES).keys()
    grade = sample["ocenyPrzedmioty"][0]["kolumnyOcenyCzastkowe"][0]["oceny"][0]
    assert grade["wpis"] == "<wpis>"
    assert grade["poprawa"] is None
