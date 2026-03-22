"""Data models for eduVulcan API responses."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Student:
    key: str  # base64-encoded student identifier, used as ?key= param
    name: str  # uczen
    class_name: str  # oddzial
    school: str  # jednostka
    diary_id: int  # idDziennik
    mailbox_key: str  # globalKeySkrzynka


@dataclass
class ClassificationPeriod:
    id: int
    number: int  # numerOkresu
    date_from: str  # dataOd (ISO 8601)
    date_to: str  # dataDo (ISO 8601)


@dataclass
class Grade:
    column_id: int  # idKolumny
    value: str  # wpis (e.g. "5p", "4+")
    date: str  # dataOceny (e.g. "26.01.2026")
    subject: str  # przedmiotNazwa (from parent)
    column_name: str  # nazwaKolumny
    category: str  # kategoriaKolumny
    weight: int  # waga
    teacher: str  # nauczyciel
    changed_since_login: bool  # zmienionaOdOstatniegoLogowania


@dataclass
class AttendanceEntry:
    lesson_number: int  # numerLekcji
    category: int  # kategoriaFrekwencji (1=present, 2=absent, ...)
    date: str  # data (ISO 8601)
    subject: str  # opisZajec
    teacher: str  # nauczyciel
    time_from: str  # godzinaOd (ISO 8601)
    time_to: str  # godzinaDo (ISO 8601)


@dataclass
class Exam:
    id: int
    date: str  # data (ISO 8601)
    subject: str  # przedmiot
    type: int  # rodzaj (1=test?, 2=quiz?)
    description: str | None = None  # opis (from detail endpoint)
    teacher: str | None = None  # nauczyciel


@dataclass
class Homework:
    id: int
    date: str  # data (ISO 8601)
    subject: str  # przedmiot
    content: str | None = None  # tresc (from detail endpoint)
    teacher: str | None = None  # nauczyciel


@dataclass
class Message:
    id: int
    api_global_key: str  # apiGlobalKey (UUID for fetching detail)
    sender: str  # korespondenci
    subject: str  # temat
    date: str  # data (ISO 8601)
    mailbox: str  # skrzynka (which kid's mailbox)
    has_attachments: bool  # hasZalaczniki
    is_read: bool  # przeczytana
    content: str | None = None  # tresc (HTML, from detail endpoint)


@dataclass
class DashboardData:
    """Combined response from all Tablica endpoints for one student."""

    grades: list[dict[str, object]] = field(default_factory=list)
    attendance: dict[str, object] = field(default_factory=dict)
    exams: list[Exam] = field(default_factory=list)
    homework: list[Homework] = field(default_factory=list)
    announcements: list[dict[str, object]] = field(default_factory=list)
    unread_messages: int = 0
