"""Parsers against the real API structure, as learned in production.

`tests/fixtures/shapes.json` is the output of `vulcan-notify shapes` on the server:
key paths and types only, no values. Each case synthesizes a response with every real
key filled in, runs the parser, and checks that the fields it must read came back
non-empty. A parser reading a key the API does not return gets its default instead,
which is the silent failure these tests exist to catch.

Refresh after a drift alert:
    ssh root@pve "pct exec 103 -- docker exec vulcan-sync uv run vulcan-notify shapes" \
        > tests/fixtures/shapes.json
"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from vulcan_notify.client import VulcanClient
from vulcan_notify.drift import synthesize
from vulcan_notify.models import ClassificationPeriod, Student

SHAPES: dict[str, dict[str, list[str]]] = json.loads(
    (Path(__file__).parent / "fixtures" / "shapes.json").read_text()
)

STUDENT = Student(key="K", name="Jan", class_name="3A", school="S", diary_id=1, mailbox_key="m")
PERIOD = ClassificationPeriod(id=1, number=1, date_from="2026-09-01", date_to="2027-01-31")


def _sample(endpoint: str) -> Any:
    # Only an empty list (or nothing at all) observed so far: there is no real item
    # structure to test against, and inventing one would defeat the point.
    if len(SHAPES.get(endpoint, {})) <= 1:
        pytest.skip(f"{endpoint} not yet observed with data; refresh shapes.json later")
    shape = {path: set(types) for path, types in SHAPES[endpoint].items()}
    return synthesize(shape)


def _client(endpoint: str, monkeypatch: pytest.MonkeyPatch) -> VulcanClient:
    monkeypatch.setattr(VulcanClient, "_jitter", staticmethod(AsyncMock()))
    resp = MagicMock()
    resp.status = 200
    resp.headers = {"content-type": "application/json"}
    resp.json = AsyncMock(return_value=_sample(endpoint))
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=resp)
    session.closed = False
    client = VulcanClient(
        {"cookies": [], "tenant": "t", "base_url": "https://uczen.eduvulcan.pl/t"}
    )
    client._http = session
    return client


def _assert_filled(items: list[Any], required: tuple[str, ...]) -> None:
    assert items, "parser returned nothing from a non-empty sample"
    item = items[0]
    assert is_dataclass(item)
    names = {f.name for f in fields(item)}
    for attr in required:
        assert attr in names, f"{type(item).__name__} has no field {attr}"
        value = getattr(item, attr)
        assert value not in ("", None, 0), (
            f"{type(item).__name__}.{attr} came back empty: the parser reads a key "
            "the real API does not return"
        )


CASES = [
    ("uczen:Context", lambda c: c.get_students(), ("key", "name", "class_name", "school")),
    (
        "uczen:OkresyKlasyfikacyjne",
        lambda c: c.get_periods(STUDENT),
        ("id", "number", "date_from", "date_to"),
    ),
    (
        "uczen:Frekwencja",
        lambda c: c.get_attendance(STUDENT, "a", "b"),
        ("lesson_number", "category", "date", "subject", "teacher", "time_from", "time_to"),
    ),
    ("uczen:SprawdzianyTablica", lambda c: c.get_exams(STUDENT), ("id", "date", "subject")),
    ("uczen:ZadaniaDomoweTablica", lambda c: c.get_homework(STUDENT), ("id", "date", "subject")),
    (
        "uczen:PlanZajec",
        lambda c: c.get_schedule(STUDENT, "a", "b"),
        ("date", "time_from", "time_to", "subject", "teacher"),
    ),
    (
        "wiadomosci:Odebrane",
        lambda c: c.get_messages(),
        ("id", "api_global_key", "sender", "subject", "date", "mailbox"),
    ),
    (
        "uczen:DniWolne",
        lambda c: c.get_days_off(STUDENT, "a", "b"),
        ("name", "date_from", "date_to"),
    ),
    (
        "uczen:Usprawiedliwienia",
        lambda c: c.get_excuses(STUDENT, "a", "b"),
        ("id", "day_id", "date", "status"),
    ),
]


@pytest.mark.parametrize(("endpoint", "call", "required"), CASES, ids=[c[0] for c in CASES])
async def test_parser_reads_real_keys(
    endpoint: str, call: Any, required: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    items = await call(_client(endpoint, monkeypatch))
    _assert_filled(items, required)


async def test_grades_parser_reads_real_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client("uczen:Oceny", monkeypatch)
    grades, summaries = await client.get_grades_and_summaries(STUDENT, PERIOD)
    _assert_filled(grades, ("column_id", "value", "date", "subject", "column_name", "teacher"))
    _assert_filled(summaries, ("subject",))


async def test_message_detail_has_body(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client("wiadomosci:WiadomoscSzczegoly", monkeypatch)
    assert await client.get_message_detail("k")


@pytest.mark.parametrize("endpoint", ["uczen:SprawdzianSzczegoly", "uczen:ZadanieDomoweSzczegoly"])
def test_detail_endpoints_carry_keys_sync_reads(endpoint: str) -> None:
    # sync.py reads these two keys off the raw detail dict.
    sample = _sample(endpoint)
    assert "opis" in sample
    assert "nauczycielImieNazwisko" in sample


def test_fixture_holds_no_values() -> None:
    for endpoint, shape in SHAPES.items():
        for path, types in shape.items():
            assert path.startswith("$"), endpoint
            assert set(types) <= {"object", "array", "string", "number", "bool", "null"}
