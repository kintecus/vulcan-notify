"""HTTP client for the eduVulcan web API (uczen.eduvulcan.pl)."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import aiohttp

from vulcan_notify.auth import _make_ssl_context, cookies_for_url
from vulcan_notify.models import (
    AttendanceEntry,
    ClassificationPeriod,
    DashboardData,
    Exam,
    Grade,
    Homework,
    Message,
    Student,
)

logger = logging.getLogger(__name__)

# Mimic a real Chrome browser to avoid bot detection
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "X-Requested-With": "XMLHttpRequest",
}

# Random delay range between requests (seconds)
_MIN_DELAY = 0.3
_MAX_DELAY = 1.5


class SessionExpiredError(Exception):
    """Raised when the session cookies are no longer valid."""


class VulcanClient:
    """Async client for the eduVulcan web API.

    Uses saved browser session cookies for authentication.
    """

    def __init__(self, session_data: dict[str, Any]) -> None:
        self._session_data = session_data
        self._base_url: str = session_data["base_url"]
        self._tenant: str = session_data.get("tenant", "")
        self._messages_base = f"https://wiadomosci.eduvulcan.pl/{self._tenant}"
        self._ssl_ctx = _make_ssl_context()
        self._http: aiohttp.ClientSession | None = None

    def _cookie_header(self) -> str:
        url = f"{self._base_url}/api/"
        cookies = cookies_for_url(self._session_data, url)
        return "; ".join(f"{k}={v}" for k, v in cookies.items())

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            headers = {**_BROWSER_HEADERS, "Cookie": self._cookie_header()}
            headers["Referer"] = f"{self._base_url}/App"
            headers["Origin"] = self._base_url
            self._http = aiohttp.ClientSession(headers=headers)
        return self._http

    @staticmethod
    async def _jitter() -> None:
        """Random delay between requests to mimic human browsing."""
        await asyncio.sleep(random.uniform(_MIN_DELAY, _MAX_DELAY))

    async def close(self) -> None:
        if self._http and not self._http.closed:
            await self._http.close()

    async def _request_url(self, url: str) -> Any:
        """Make a GET request to an absolute URL. Returns parsed JSON.

        Builds a per-request Cookie header matching the URL's domain,
        since different subdomains need different cookies.
        """
        await self._jitter()
        session = await self._ensure_session()
        cookie_header = "; ".join(
            f"{k}={v}" for k, v in cookies_for_url(self._session_data, url).items()
        )

        async with session.get(
            url, ssl=self._ssl_ctx, headers={"Cookie": cookie_header, "Referer": url}
        ) as resp:
            content_type = resp.headers.get("content-type", "")

            if "text/html" in content_type:
                raise SessionExpiredError(
                    "Session expired. Run 'vulcan-notify auth' to re-authenticate."
                )

            if resp.status != 200:
                text = await resp.text()
                logger.warning("API error %d for %s: %s", resp.status, url, text[:200])
                return None

            return await resp.json()

    async def _request(self, path: str) -> Any:
        """Make a GET request to the API. Returns parsed JSON.

        Raises SessionExpiredError if the response is HTML (login redirect).
        """
        await self._jitter()
        session = await self._ensure_session()
        url = f"{self._base_url}{path}"

        async with session.get(url, ssl=self._ssl_ctx) as resp:
            content_type = resp.headers.get("content-type", "")

            if "text/html" in content_type:
                raise SessionExpiredError(
                    "Session expired. Run 'vulcan-notify auth' to re-authenticate."
                )

            if resp.status != 200:
                text = await resp.text()
                logger.warning("API error %d for %s: %s", resp.status, path, text[:200])
                return None

            return await resp.json()

    # ── Student context ──────────────────────────────────────────────

    async def get_students(self) -> list[Student]:
        data = await self._request("/api/Context")
        if not data or "uczniowie" not in data:
            return []

        return [
            Student(
                key=s["key"],
                name=s["uczen"],
                class_name=s["oddzial"],
                school=s["jednostka"],
                diary_id=s["idDziennik"],
                mailbox_key=s.get("globalKeySkrzynka", ""),
            )
            for s in data["uczniowie"]
            if s.get("aktywny", True)
        ]

    # ── Grades ───────────────────────────────────────────────────────

    async def get_periods(self, student: Student) -> list[ClassificationPeriod]:
        data = await self._request(
            f"/api/OkresyKlasyfikacyjne?key={student.key}&idDziennik={student.diary_id}"
        )
        if not data:
            return []

        return [
            ClassificationPeriod(
                id=p["id"],
                number=p["numerOkresu"],
                date_from=p["dataOd"],
                date_to=p["dataDo"],
            )
            for p in data
        ]

    async def get_grades(self, student: Student, period: ClassificationPeriod) -> list[Grade]:
        data = await self._request(
            f"/api/Oceny?key={student.key}&idOkresKlasyfikacyjny={period.id}"
        )
        if not data or "ocenyPrzedmioty" not in data:
            return []

        grades: list[Grade] = []
        for subject in data["ocenyPrzedmioty"]:
            subject_name = subject.get("przedmiotNazwa", "")
            for column in subject.get("kolumnyOcenyCzastkowe") or []:
                for grade in column.get("oceny", []):
                    grades.append(
                        Grade(
                            column_id=grade.get("idKolumny", column.get("idKolumny", 0)),
                            value=grade.get("wpis", ""),
                            date=grade.get("dataOceny", ""),
                            subject=subject_name,
                            column_name=grade.get("nazwaKolumny", column.get("nazwaKolumny", "")),
                            category=grade.get(
                                "kategoriaKolumny", column.get("kategoriaKolumny", "")
                            ),
                            weight=grade.get("waga", 1),
                            teacher=grade.get("nauczyciel", ""),
                            changed_since_login=grade.get("zmienionaOdOstatniegoLogowania", False),
                        )
                    )
        return grades

    # ── Attendance ───────────────────────────────────────────────────

    async def get_attendance(
        self, student: Student, date_from: str, date_to: str
    ) -> list[AttendanceEntry]:
        """Fetch attendance. date_from/date_to are ISO 8601 strings."""
        data = await self._request(
            f"/api/Frekwencja?key={student.key}&dataOd={date_from}&dataDo={date_to}"
        )
        if not data:
            return []

        entries: list[AttendanceEntry] = []
        for entry in data.get("oddzialy", []):
            entries.append(
                AttendanceEntry(
                    lesson_number=entry.get("numerLekcji", 0),
                    category=entry.get("kategoriaFrekwencji", 0),
                    date=entry.get("data", ""),
                    subject=entry.get("opisZajec", ""),
                    teacher=entry.get("nauczyciel", ""),
                    time_from=entry.get("godzinaOd", ""),
                    time_to=entry.get("godzinaDo", ""),
                )
            )
        return entries

    # ── Dashboard (Tablica) endpoints ────────────────────────────────

    async def get_exams(self, student: Student) -> list[Exam]:
        data = await self._request(f"/api/SprawdzianyTablica?key={student.key}")
        if not data:
            return []
        return [
            Exam(
                id=e["id"],
                date=e.get("data", ""),
                subject=e.get("przedmiot", ""),
                type=e.get("rodzaj", 0),
            )
            for e in data
        ]

    async def get_homework(self, student: Student) -> list[Homework]:
        data = await self._request(f"/api/ZadaniaDomoweTablica?key={student.key}")
        if not data:
            return []
        return [
            Homework(
                id=h["id"],
                date=h.get("data", ""),
                subject=h.get("przedmiot", ""),
            )
            for h in data
        ]

    async def get_dashboard(self, student: Student) -> DashboardData:
        """Fetch all dashboard (Tablica) data concurrently for a student."""
        grades_task = self._request(f"/api/OcenyTablica?key={student.key}")
        attendance_task = self._request(f"/api/FrekwencjaTablica?key={student.key}")
        exams_task = self.get_exams(student)
        homework_task = self.get_homework(student)
        announcements_task = self._request(f"/api/OgloszeniaTablica?key={student.key}")
        messages_task = self._request("/api/WiadomosciNieodczytane")

        results = await asyncio.gather(
            grades_task,
            attendance_task,
            exams_task,
            homework_task,
            announcements_task,
            messages_task,
            return_exceptions=True,
        )

        def safe_result(r: Any, default: Any = None) -> Any:
            if isinstance(r, Exception):
                logger.warning("Dashboard fetch failed: %s", r)
                return default
            return r if r is not None else default

        unread_data = safe_result(results[5], {})
        unread_count = (
            unread_data.get("liczbaWiadomosciNieodczytanych", 0)
            if isinstance(unread_data, dict)
            else 0
        )

        return DashboardData(
            grades=safe_result(results[0], []),
            attendance=safe_result(results[1], {}),
            exams=safe_result(results[2], []),
            homework=safe_result(results[3], []),
            announcements=safe_result(results[4], []),
            unread_messages=unread_count,
        )

    # ── Messages (wiadomosci.eduvulcan.pl) ───────────────────────────

    async def get_messages(self, page_size: int = 50) -> list[Message]:
        """Fetch received messages from the messages subdomain.

        Returns up to page_size most recent messages (unified inbox).
        """
        data = await self._request_url(
            f"{self._messages_base}/api/Odebrane?idLastWiadomosc=0&pageSize={page_size}"
        )
        if not data:
            return []

        return [
            Message(
                id=m["id"],
                api_global_key=m.get("apiGlobalKey", ""),
                sender=m.get("korespondenci", ""),
                subject=m.get("temat", "").strip(),
                date=m.get("data", ""),
                mailbox=m.get("skrzynka", ""),
                has_attachments=m.get("hasZalaczniki", False),
                is_read=m.get("przeczytana", False),
            )
            for m in data
        ]

    async def get_message_detail(self, api_global_key: str) -> str | None:
        """Fetch full message content (HTML) by its apiGlobalKey."""
        data = await self._request_url(
            f"{self._messages_base}/api/WiadomoscSzczegoly?apiGlobalKey={api_global_key}"
        )
        if not data:
            return None
        return data.get("tresc")
