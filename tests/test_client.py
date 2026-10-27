"""Tests for the eduVulcan API client."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from vulcan_notify.client import SessionExpiredError, VulcanClient
from vulcan_notify.models import Student


@pytest.fixture
def session_data() -> dict:
    return {
        "cookies": [
            {
                "name": "ASP.NET_SessionId",
                "value": "abc123",
                "domain": "uczen.eduvulcan.pl",
                "path": "/",
            },
            {
                "name": "Eduvulcan.Uczen.Sso",
                "value": "token123",
                "domain": "uczen.eduvulcan.pl",
                "path": "/",
            },
        ],
        "tenant": "testdistrict",
        "base_url": "https://uczen.eduvulcan.pl/testdistrict",
    }


@pytest.fixture
def student() -> Student:
    return Student(
        key="TESTKEY123",
        name="Jan Kowalski",
        class_name="3A",
        school="Szkola Testowa",
        diary_id=1001,
        mailbox_key="aaaa-bbbb",
    )


def _mock_response(
    json_data: object,
    content_type: str = "application/json; charset=utf-8",
    status: int = 200,
) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.headers = {"content-type": content_type}
    resp.json = AsyncMock(return_value=json_data)
    resp.text = AsyncMock(return_value="<html>Error</html>")
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _mock_session(response: MagicMock) -> MagicMock:
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    session.closed = False
    session.close = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


async def test_get_students(session_data: dict) -> None:
    context_response = {
        "uczniowie": [
            {
                "key": "KEY1",
                "uczen": "Jan Kowalski",
                "oddzial": "3A",
                "jednostka": "Szkola nr 1",
                "idDziennik": 1001,
                "globalKeySkrzynka": "aaa-bbb",
                "aktywny": True,
            },
            {
                "key": "KEY2",
                "uczen": "Anna Kowalska",
                "oddzial": "5B",
                "jednostka": "Szkola nr 1",
                "idDziennik": 1002,
                "globalKeySkrzynka": "ccc-ddd",
                "aktywny": True,
            },
        ]
    }

    mock_resp = _mock_response(context_response)
    mock_sess = _mock_session(mock_resp)

    client = VulcanClient(session_data)
    client._http = mock_sess

    students = await client.get_students()
    assert len(students) == 2
    assert students[0].name == "Jan Kowalski"
    assert students[0].key == "KEY1"
    assert students[1].class_name == "5B"


async def test_get_students_empty(session_data: dict) -> None:
    mock_resp = _mock_response({"uczniowie": []})
    mock_sess = _mock_session(mock_resp)

    client = VulcanClient(session_data)
    client._http = mock_sess

    students = await client.get_students()
    assert students == []


async def test_session_expired_raises(session_data: dict) -> None:
    mock_resp = _mock_response(None, content_type="text/html; charset=utf-8")
    mock_sess = _mock_session(mock_resp)

    client = VulcanClient(session_data)
    client._http = mock_sess

    with pytest.raises(SessionExpiredError):
        await client.get_students()


async def test_get_grades(session_data: dict, student: Student) -> None:
    from vulcan_notify.models import ClassificationPeriod

    grades_response = {
        "ocenyPrzedmioty": [
            {
                "przedmiotNazwa": "Matematyka",
                "kolumnyOcenyCzastkowe": [
                    {
                        "idKolumny": 100,
                        "kategoriaKolumny": "Biezace",
                        "nazwaKolumny": "Sprawdzian 1",
                        "oceny": [
                            {
                                "idKolumny": 100,
                                "wpis": "5",
                                "dataOceny": "15.03.2026",
                                "kategoriaKolumny": "Biezace",
                                "nazwaKolumny": "Sprawdzian 1",
                                "waga": 2,
                                "nauczyciel": "Nowak A.",
                                "zmienionaOdOstatniegoLogowania": False,
                            }
                        ],
                    }
                ],
            }
        ]
    }

    mock_resp = _mock_response(grades_response)
    mock_sess = _mock_session(mock_resp)

    client = VulcanClient(session_data)
    client._http = mock_sess

    period = ClassificationPeriod(id=1, number=2, date_from="2026-02-01", date_to="2026-08-31")
    grades = await client.get_grades(student, period)

    assert len(grades) == 1
    assert grades[0].value == "5"
    assert grades[0].subject == "Matematyka"
    assert grades[0].teacher == "Nowak A."
    assert grades[0].weight == 2


async def test_get_grades_null_category_coalesced(session_data: dict, student: Student) -> None:
    """Vulcan sometimes returns an explicit null for kategoriaKolumny/nazwaKolumny/wpis.

    dict.get(key, default) returns None (not the default) on an explicit null, and
    these columns are NOT NULL in the DB. Left as None they raise IntegrityError in
    upsert_grade, the grade never persists, and it re-publishes to MQTT every sync
    cycle. The parser must coalesce explicit nulls to "".
    """
    from vulcan_notify.models import ClassificationPeriod

    grades_response = {
        "ocenyPrzedmioty": [
            {
                "przedmiotNazwa": "Matematyka",
                "kolumnyOcenyCzastkowe": [
                    {
                        "idKolumny": 200,
                        "kategoriaKolumny": None,
                        "nazwaKolumny": None,
                        "oceny": [
                            {
                                "idKolumny": 200,
                                "wpis": None,
                                "dataOceny": "20.06.2026",
                                "kategoriaKolumny": None,
                                "nazwaKolumny": None,
                                "waga": 1,
                                "nauczyciel": "Nowak A.",
                                "zmienionaOdOstatniegoLogowania": False,
                            }
                        ],
                    }
                ],
            }
        ]
    }

    mock_resp = _mock_response(grades_response)
    mock_sess = _mock_session(mock_resp)

    client = VulcanClient(session_data)
    client._http = mock_sess

    period = ClassificationPeriod(id=1, number=2, date_from="2026-02-01", date_to="2026-08-31")
    grades = await client.get_grades(student, period)

    assert len(grades) == 1
    assert grades[0].category == ""
    assert grades[0].column_name == ""
    assert grades[0].value == ""


async def test_get_grades_empty(session_data: dict, student: Student) -> None:
    from vulcan_notify.models import ClassificationPeriod

    mock_resp = _mock_response({"ocenyPrzedmioty": []})
    mock_sess = _mock_session(mock_resp)

    client = VulcanClient(session_data)
    client._http = mock_sess

    period = ClassificationPeriod(id=1, number=2, date_from="2026-02-01", date_to="2026-08-31")
    grades = await client.get_grades(student, period)
    assert grades == []


async def test_get_exams(session_data: dict, student: Student) -> None:
    exams_response = [
        {"id": 10001, "data": "2026-03-16T00:00:00+01:00", "przedmiot": "Przyroda", "rodzaj": 2}
    ]

    mock_resp = _mock_response(exams_response)
    mock_sess = _mock_session(mock_resp)

    client = VulcanClient(session_data)
    client._http = mock_sess

    exams = await client.get_exams(student)
    assert len(exams) == 1
    assert exams[0].subject == "Przyroda"
    assert exams[0].type == 2


async def test_get_homework(session_data: dict, student: Student) -> None:
    hw_response = [{"id": 10002, "data": "2026-03-16T00:00:00+01:00", "przedmiot": "Plastyka"}]

    mock_resp = _mock_response(hw_response)
    mock_sess = _mock_session(mock_resp)

    client = VulcanClient(session_data)
    client._http = mock_sess

    homework = await client.get_homework(student)
    assert len(homework) == 1
    assert homework[0].subject == "Plastyka"


async def test_get_attendance(session_data: dict, student: Student) -> None:
    attendance_response = {
        "oddzialy": [
            {
                "numerLekcji": 3,
                "kategoriaFrekwencji": 2,
                "data": "2026-03-14T00:00:00+01:00",
                "opisZajec": "Przyroda",
                "nauczyciel": "Wisniewski T.",
                "godzinaOd": "2026-03-14T09:50:00+01:00",
                "godzinaDo": "2026-03-14T10:35:00+01:00",
            }
        ]
    }

    mock_resp = _mock_response(attendance_response)
    mock_sess = _mock_session(mock_resp)

    client = VulcanClient(session_data)
    client._http = mock_sess

    entries = await client.get_attendance(
        student, "2026-03-13T23:00:00.000Z", "2026-03-15T22:59:59.999Z"
    )
    assert len(entries) == 1
    assert entries[0].category == 2
    assert entries[0].subject == "Przyroda"


@pytest.fixture
def fast_client(session_data: dict, monkeypatch: pytest.MonkeyPatch) -> VulcanClient:
    monkeypatch.setattr(VulcanClient, "_jitter", staticmethod(AsyncMock()))
    return VulcanClient(session_data)


async def test_fetch_records_shape_per_endpoint(fast_client: VulcanClient) -> None:
    fast_client._http = _mock_session(_mock_response([{"id": 1, "nazwa": "Ferie"}]))
    await fast_client._request("/api/DniWolne?key=K&dataOd=a&dataDo=b")
    shape = fast_client.shapes["uczen:DniWolne"]
    assert shape["$[].nazwa"] == {"string"}
    assert "Ferie" not in repr(fast_client.shapes)


async def test_get_days_off(fast_client: VulcanClient, student: Student) -> None:
    payload = [
        {
            "nazwa": "Ferie zimowe",
            "dataOd": "2027-01-19T00:00:00",
            "dataDo": "2027-02-01T00:00:00",
            "wszystkieSkladowe": True,
            "jednostkiSkladowe": [],
        }
    ]
    fast_client._http = _mock_session(_mock_response(payload))
    days = await fast_client.get_days_off(student, "a", "b")
    assert days[0].name == "Ferie zimowe"
    assert days[0].date_to.startswith("2027-02-01")


async def test_get_excuses(fast_client: VulcanClient, student: Student) -> None:
    payload = {
        "usprawiedliwienia": [
            {
                "idUsprawiedliwienieDzienPoraLekcji": 5,
                "idUsprawiedliwienieDzien": 50,
                "dzien": "2026-10-01T00:00:00",
                "numerLekcji": None,
                "status": 1,
            }
        ],
        "usprawiedliwieniaAktywne": True,
        "usprawiedliwieniaBlokada": None,
    }
    fast_client._http = _mock_session(_mock_response(payload))
    excuses = await fast_client.get_excuses(student, "a", "b")
    assert excuses[0].id == 5
    assert excuses[0].lesson_number is None
    assert excuses[0].status == 1


async def test_get_excuses_without_key_raises(fast_client: VulcanClient, student: Student) -> None:
    from vulcan_notify.client import VulcanFetchError

    fast_client._http = _mock_session(_mock_response({"somethingElse": []}))
    with pytest.raises(VulcanFetchError):
        await fast_client.get_excuses(student, "a", "b")


async def test_get_remarks_best_effort_fields(fast_client: VulcanClient, student: Student) -> None:
    payload = [{"tresc": "Brak stroju", "nauczyciel": "Nowak A.", "data": "2026-10-01"}]
    fast_client._http = _mock_session(_mock_response(payload))
    [item] = await fast_client.get_remarks(student)
    assert (item.kind, item.text, item.author) == ("remark", "Brak stroju", "Nowak A.")
    assert len(item.key) == 16  # no id, so a stable hash of the item


async def test_get_announcements_empty(fast_client: VulcanClient, student: Student) -> None:
    fast_client._http = _mock_session(_mock_response([]))
    assert await fast_client.get_announcements(student) == []


async def test_get_messages_passes_cursor(fast_client: VulcanClient) -> None:
    sess = _mock_session(_mock_response([]))
    fast_client._http = sess
    await fast_client.get_messages(page_size=50, last_id=379162)
    url = sess.get.call_args.args[0]
    assert "idLastWiadomosc=379162" in url
    assert "pageSize=50" in url
