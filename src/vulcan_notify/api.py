"""Tiny HTTP API for Home Assistant integration."""

from __future__ import annotations

import calendar
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from aiohttp import web

from vulcan_notify.config import settings
from vulcan_notify.freshness import ages, next_wakeup

logger = logging.getLogger(__name__)


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(str(settings.db_path))
    db.row_factory = sqlite3.Row
    return db


# Data sections tracked for freshness. Mirrors db.SECTIONS; duplicated rather than
# imported because this module talks to SQLite synchronously and deliberately does
# not pull in the aiosqlite Database class.
_SECTIONS = ("grades", "attendance", "exams", "homework", "schedule", "messages")


def _get_health() -> dict[str, Any]:
    """Compute the freshness picture from sync_runs + sync_state.

    Freshness comes from `last_success:<student>:<section>` keys, which sync.py only
    writes after a confirmed fetch. It is deliberately not derived from "did the loop
    run" -- the loop running while every fetch returns nothing is the failure this
    whole endpoint exists to catch.
    """
    now = datetime.now()
    stale_after = settings.stale_after_seconds

    try:
        db = _connect()
    except sqlite3.Error as exc:
        return {
            "status": "failed",
            "stale": True,
            "error": f"cannot open database: {exc}",
            "generated_at": now.isoformat(),
        }

    try:
        row = db.execute(
            "SELECT id, started_at, completed_at, status, students_synced, "
            "items_processed, errors_count, error_detail "
            "FROM sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_run = dict(row) if row else None

        active_keys = [r[0] for r in db.execute("SELECT key FROM students WHERE active = 1")]
        stamps = {
            r[0]: r[1]
            for r in db.execute("SELECT key, value FROM sync_state WHERE key LIKE 'last_success:%'")
        }
    finally:
        db.close()

    sections: dict[str, Any] = {}
    for section in _SECTIONS:
        # Messages are account-wide; everything else is per student. A section is
        # only as fresh as its stalest active student.
        keys = [""] if section == "messages" else active_keys
        pairs = [ages(stamps.get(f"last_success:{k}:{section}"), now) for k in keys] or [None]
        if any(p is None for p in pairs):
            age: float | None = None
            effective: float | None = None
        else:
            age = max(p[0] for p in pairs if p is not None)
            effective = max(p[1] for p in pairs if p is not None)
        sections[section] = {
            "age_seconds": None if age is None else int(age),
            # Compared on effective age: a quiet-hours pause is not staleness.
            "stale": True if effective is None else effective > stale_after,
        }

    stale = [name for name, s in sections.items() if s["stale"]]
    run_status = str(last_run["status"]) if last_run else "unknown"

    if not last_run or len(stale) == len(_SECTIONS):
        status = "failed"
    elif stale:
        status = "stale"
    elif run_status in ("degraded", "failed", "interrupted"):
        status = "degraded"
    else:
        status = "ok"

    known_ages: list[int] = [
        s["age_seconds"] for s in sections.values() if s["age_seconds"] is not None
    ]

    # Surfaced so an idle-but-healthy service says why it is idle. Without it, a
    # five-hour-old age_seconds next to status "ok" reads like a bug in the check.
    resume_at = next_wakeup(now)

    return {
        "status": status,
        "stale": status in ("stale", "failed"),
        "stale_sections": stale,
        "age_seconds": max(known_ages) if known_ages else None,
        "stale_after_seconds": stale_after,
        "quiet_hours": {
            "start": settings.quiet_hours_start,
            "end": settings.quiet_hours_end,
            "active": resume_at is not None,
            "resumes_at": resume_at.isoformat() if resume_at else None,
        },
        "sections": sections,
        "last_run": last_run,
        "generated_at": now.isoformat(),
    }


def _meta(*sections: str) -> dict[str, Any]:
    """Provenance block attached to every data response.

    Added as a top-level `_meta` key rather than wrapping the payload in an envelope:
    every Home Assistant REST sensor scopes `json_attributes_path` to a student key,
    so an extra sibling key is invisible to them and no HA config had to change.
    """
    health = _get_health()
    relevant = {s: health.get("sections", {}).get(s) for s in sections if s in _SECTIONS}
    stale = any(v and v["stale"] for v in relevant.values()) if relevant else health["stale"]
    ages = [v["age_seconds"] for v in relevant.values() if v and v["age_seconds"] is not None]

    return {
        "status": health["status"],
        "stale": stale,
        "age_seconds": max(ages) if ages else health.get("age_seconds"),
        "sections": relevant,
        "generated_at": health["generated_at"],
    }


Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@web.middleware
async def error_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Turn unhandled faults into structured responses.

    Query params are parsed with bare int(), so `?n=abc` used to surface as a 500 with
    a traceback. A locked or missing database did the same. Neither is distinguishable
    from a server bug by anything upstream.
    """
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except ValueError as exc:
        return web.json_response({"error": "bad request", "detail": str(exc)}, status=400)
    except sqlite3.Error as exc:
        logger.exception("Database error serving %s", request.path)
        return web.json_response(
            {"error": "database unavailable", "detail": str(exc)}, status=503
        )
    except Exception as exc:
        # Last resort: a handler bug must produce a structured 500, not a raw traceback.
        logger.exception("Unhandled error serving %s", request.path)
        return web.json_response({"error": "internal error", "detail": str(exc)}, status=500)


def _date_minus_days(iso_date: str, days: int) -> str:
    """Return ISO date string shifted back by N days."""
    dt = datetime.strptime(iso_date, "%Y-%m-%d") - timedelta(days=days)
    return dt.strftime("%Y-%m-%d")


def _is_diagnostic(value: str) -> bool:
    """Diagnostic 'diagnoza' results are stored as percentages like '35 (%)' or '86%'."""
    return "%" in value


# Plus/minus modifier values. Vulcan UONET+ defaults are +0.25 / -0.25 but each
# school can override in Administration → "Wartości znaków +,–,=". The current
# values match what Vulcan's monthly average chart shows for Solomiia's grades
# (verified against Feb/Mar/Apr/May 2026 readings within ±0.10).
_PLUS_DELTA = 0.5
_MINUS_DELTA = 0.25


def _grade_to_numeric(value: str) -> float | None:
    """Convert Polish grade string to numeric value. Returns None for non-gradeable marks."""
    v = value.strip().lower()
    if _is_diagnostic(v):
        return None
    if not v or v[0] not in "123456":
        if v and v != "nc":
            logger.warning("Unparseable grade value: %r", value)
        return None
    base = int(v[0])
    if len(v) == 1 or v[1] == "p":
        return float(base)
    if v[1] == "+":
        return base + _PLUS_DELTA
    if v[1] == "-":
        return base - _MINUS_DELTA
    return float(base)


def _resolve_period_id(
    db: sqlite3.Connection, student_key: str, period_request: str | None
) -> int | None:
    """Resolve a period_id for a student.

    `period_request` may be:
    - None or "current" → the period whose date range contains today, or the
      most recent period if today is between periods.
    - "all" → None (caller should treat as no filter)
    - an integer string → that exact period_id
    - "1" / "2" / "okres1" / "okres2" → match on ClassificationPeriod.number

    Returns None when no filter should apply or no period matches.
    """
    if period_request == "all":
        return None

    row = db.execute(
        "SELECT period_id, number, date_from, date_to FROM classification_periods "
        "WHERE student_key = ? ORDER BY date_from",
        (student_key,),
    ).fetchall()
    if not row:
        return None

    if period_request and period_request.isdigit() and len(period_request) > 1:
        # Looks like an explicit period_id
        for p in row:
            if p["period_id"] == int(period_request):
                return p["period_id"]
        return None

    # Match on okres number (1, 2, ...)
    if period_request in ("1", "2", "okres1", "okres2", "okres 1", "okres 2"):
        wanted = int(period_request[-1])
        for p in row:
            if p["number"] == wanted:
                return p["period_id"]
        return None

    # Default: current period (date range contains today, else latest)
    today = datetime.now().strftime("%Y-%m-%d")
    for p in row:
        if p["date_from"] <= today <= p["date_to"]:
            return p["period_id"]
    return row[-1]["period_id"]


def _get_grade_averages(
    student_filter: str | None = None,
    window_days: int = 30,
    period_request: str | None = None,
) -> dict[str, Any]:
    """Compute weighted grade averages per student with rolling window time series."""
    db = _connect()
    result: dict[str, Any] = {}

    query = "SELECT key, name FROM students WHERE active = 1"
    params: tuple[str, ...] = ()
    if student_filter:
        query += " AND name = ?"
        params = (student_filter,)

    for s in db.execute(query, params):
        period_id = _resolve_period_id(db, s["key"], period_request)
        sql = (
            "SELECT value, date, weight FROM grades "
            "WHERE student_key = ? AND superseded_by_grade_id IS NULL"
        )
        sql_params: list[object] = [s["key"]]
        if period_id is not None:
            sql += " AND period_id = ?"
            sql_params.append(period_id)
        sql += " ORDER BY substr(date,7,4)||substr(date,4,2)||substr(date,1,2) ASC"
        grades = db.execute(sql, sql_params).fetchall()

        # Parse all grades into a list with ISO dates
        parsed: list[tuple[str, float, int]] = []
        for g in grades:
            numeric = _grade_to_numeric(g["value"])
            if numeric is None:
                continue
            raw_date = g["date"]
            iso_date = f"{raw_date[6:10]}-{raw_date[3:5]}-{raw_date[0:2]}"
            parsed.append((iso_date, numeric, g["weight"] or 1))

        # Compute rolling window average at each grade date
        timeline: list[dict[str, Any]] = []
        for i, (date, _, _) in enumerate(parsed):
            cutoff = _date_minus_days(date, window_days)
            w_sum = 0.0
            wt_sum = 0
            for d, val, w in parsed[: i + 1]:
                if d >= cutoff:
                    w_sum += val * w
                    wt_sum += w
            if wt_sum:
                timeline.append(
                    {
                        "date": date,
                        "average": round(w_sum / wt_sum, 2),
                    }
                )

        # Current overall weighted average (all time)
        total_w_sum = sum(v * w for _, v, w in parsed)
        total_wt = sum(w for _, _, w in parsed)

        result[s["name"]] = {
            "average": round(total_w_sum / total_wt, 2) if total_wt else None,
            "rolling_average": timeline[-1]["average"] if timeline else None,
            "window_days": window_days,
            "count": len(parsed),
            "grades_over_time": timeline,
        }

    db.close()
    return result


def _month_list(year: int | None, months: int) -> list[str]:
    """Return ordered list of YYYY-MM strings for the requested range."""
    if year is not None:
        return [f"{year:04d}-{m:02d}" for m in range(1, 13)]
    now = datetime.now()
    out: list[str] = []
    y, m = now.year, now.month
    for _ in range(months):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    out.reverse()
    return out


def _get_monthly_averages(
    student_filter: str | None = None,
    year: int | None = None,
    months: int = 6,
) -> dict[str, Any]:
    """Compute weighted grade averages grouped by calendar month per student."""
    db = _connect()
    result: dict[str, Any] = {}

    query = "SELECT key, name FROM students WHERE active = 1"
    params: tuple[str, ...] = ()
    if student_filter:
        query += " AND name = ?"
        params = (student_filter,)

    month_keys = _month_list(year, months)

    for s in db.execute(query, params):
        # Monthly chart shows all months regardless of semester; just skip
        # superseded (improvement-original) rows.
        grades = db.execute(
            "SELECT value, date, weight FROM grades "
            "WHERE student_key = ? AND superseded_by_grade_id IS NULL",
            (s["key"],),
        ).fetchall()

        buckets: dict[str, tuple[float, int, int]] = {k: (0.0, 0, 0) for k in month_keys}
        for g in grades:
            numeric = _grade_to_numeric(g["value"])
            if numeric is None:
                continue
            raw_date = g["date"]
            month_key = f"{raw_date[6:10]}-{raw_date[3:5]}"
            if month_key not in buckets:
                continue
            w = g["weight"] or 1
            w_sum, wt_sum, count = buckets[month_key]
            buckets[month_key] = (w_sum + numeric * w, wt_sum + w, count + 1)

        month_rows: list[dict[str, Any]] = []
        for key in month_keys:
            w_sum, wt_sum, count = buckets[key]
            avg = round(w_sum / wt_sum, 2) if wt_sum else None
            month_num = int(key[5:7])
            month_rows.append(
                {
                    "month": key,
                    "label": calendar.month_abbr[month_num],
                    "average": avg,
                    "count": count,
                }
            )

        result[s["name"]] = {"months": month_rows}

    db.close()
    return result


def _get_subject_averages(
    student_filter: str | None = None,
    period_request: str | None = None,
) -> dict[str, Any]:
    """Compute weighted grade averages grouped by subject, sorted descending."""
    db = _connect()
    result: dict[str, Any] = {}

    query = "SELECT key, name FROM students WHERE active = 1"
    params: tuple[str, ...] = ()
    if student_filter:
        query += " AND name = ?"
        params = (student_filter,)

    for s in db.execute(query, params):
        period_id = _resolve_period_id(db, s["key"], period_request)
        sql = (
            "SELECT value, subject, weight FROM grades "
            "WHERE student_key = ? AND superseded_by_grade_id IS NULL"
        )
        sql_params: list[object] = [s["key"]]
        if period_id is not None:
            sql += " AND period_id = ?"
            sql_params.append(period_id)
        grades = db.execute(sql, sql_params).fetchall()

        buckets: dict[str, tuple[float, int, int]] = {}
        for g in grades:
            numeric = _grade_to_numeric(g["value"])
            if numeric is None:
                continue
            subject = g["subject"]
            w = g["weight"] or 1
            w_sum, wt_sum, count = buckets.get(subject, (0.0, 0, 0))
            buckets[subject] = (w_sum + numeric * w, wt_sum + w, count + 1)

        rows = [
            {
                "subject": subject,
                "average": round(w_sum / wt_sum, 2),
                "count": count,
            }
            for subject, (w_sum, wt_sum, count) in buckets.items()
            if wt_sum
        ]
        rows.sort(key=lambda r: r["average"], reverse=True)
        result[s["name"]] = {"subjects": rows}

    db.close()
    return result


def _get_subject_summaries(
    student_filter: str | None = None,
    period_request: str | None = None,
) -> dict[str, Any]:
    """Return per-subject end-of-term roll-ups (final + proposed grade) for a period."""
    db = _connect()
    result: dict[str, Any] = {}

    query = "SELECT key, name FROM students WHERE active = 1"
    params: tuple[str, ...] = ()
    if student_filter:
        query += " AND name = ?"
        params = (student_filter,)

    for s in db.execute(query, params):
        period_id = _resolve_period_id(db, s["key"], period_request)
        sql = (
            "SELECT subject, final_grade, proposed_final_grade, use_weighted_average "
            "FROM subject_summaries WHERE student_key = ?"
        )
        sql_params: list[object] = [s["key"]]
        if period_id is not None:
            sql += " AND period_id = ?"
            sql_params.append(period_id)
        sql += " ORDER BY subject"

        rows = [
            {
                "subject": r["subject"],
                "final_grade": r["final_grade"],
                "proposed_final_grade": r["proposed_final_grade"],
                "use_weighted_average": bool(r["use_weighted_average"]),
            }
            for r in db.execute(sql, sql_params)
        ]
        # Resolve period metadata for the response so HA templates know which
        # term they're looking at without a second call.
        period_meta = None
        if period_id is not None:
            p = db.execute(
                "SELECT period_id, number, date_from, date_to "
                "FROM classification_periods WHERE student_key = ? AND period_id = ?",
                (s["key"], period_id),
            ).fetchone()
            if p:
                period_meta = {
                    "id": p["period_id"],
                    "number": p["number"],
                    "date_from": p["date_from"],
                    "date_to": p["date_to"],
                }
        result[s["name"]] = {"period": period_meta, "subjects": rows}

    db.close()
    return result


def _get_schedule(
    student_filter: str | None = None,
    only_substitutions: bool = False,
    days_ahead: int = 14,
) -> dict[str, Any]:
    """Return upcoming lessons per student, newest first.

    With `only_substitutions=True`, returns only lessons where a substitution
    has been recorded.
    """
    db = _connect()
    result: dict[str, Any] = {}

    today = datetime.now().strftime("%Y-%m-%d")
    to_date = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    query = "SELECT key, name FROM students WHERE active = 1"
    params: tuple[str, ...] = ()
    if student_filter:
        query += " AND name = ?"
        params = (student_filter,)

    for s in db.execute(query, params):
        sql = (
            "SELECT date, time_from, time_to, subject, teacher, room, group_name, "
            "annotation, is_extra, sub_teacher, sub_room, sub_type, absence_info, remarks "
            "FROM schedule WHERE student_key = ? AND date >= ? AND date <= ?"
        )
        row_params: list[object] = [s["key"], today, to_date]
        if only_substitutions:
            sql += (
                " AND (sub_teacher IS NOT NULL OR (sub_room IS NOT NULL AND sub_room != '') "
                "OR remarks IS NOT NULL OR annotation != 0)"
            )
        sql += " ORDER BY date ASC, time_from ASC"

        rows = db.execute(sql, row_params).fetchall()
        lessons = [
            {
                "date": r["date"],
                "time_from": r["time_from"],
                "time_to": r["time_to"],
                "subject": r["subject"],
                "teacher": r["teacher"],
                "room": r["room"],
                "group": r["group_name"],
                "is_extra": bool(r["is_extra"]),
                "sub_teacher": r["sub_teacher"],
                "sub_room": r["sub_room"],
                "sub_type": r["sub_type"],
                "absence_info": r["absence_info"],
                "remarks": r["remarks"],
            }
            for r in rows
        ]
        result[s["name"]] = {"lessons": lessons, "count": len(lessons)}

    db.close()
    return result


def _get_grades(n: int = 5, diagnostic_days: int = 180) -> dict[str, Any]:
    """Read latest N grades per student plus recent diagnostic-test results.

    Diagnostics (Polish 'diagnoza' tests, scored as raw percentages e.g. '35 (%)')
    are surfaced separately so the dashboard can flag them without polluting the
    regular grade stream — they don't count toward the semester average.
    """
    db = _connect()
    students = {}
    diag_cutoff = _date_minus_days(datetime.now().strftime("%Y-%m-%d"), diagnostic_days)
    for s in db.execute("SELECT key, name, class_name FROM students WHERE active = 1"):
        grades = []
        non_diag_count = 0
        diagnostics: list[dict[str, Any]] = []
        for g in db.execute(
            "SELECT value, date, subject, column_name, category "
            "FROM grades WHERE student_key = ? "
            "ORDER BY substr(date,7,4)||substr(date,4,2)||substr(date,1,2) DESC ",
            (s["key"],),
        ):
            row = dict(g)
            iso = f"{row['date'][6:10]}-{row['date'][3:5]}-{row['date'][0:2]}"
            if _is_diagnostic(row["value"]):
                if iso >= diag_cutoff:
                    diagnostics.append(row)
                continue
            if non_diag_count < n:
                grades.append(row)
                non_diag_count += 1
        students[s["name"]] = {
            "class": s["class_name"],
            "grades": grades,
            "diagnostics": diagnostics,
        }
    db.close()
    return students


def _get_homework(n: int = 5) -> dict[str, Any]:
    """Read latest N homework items per student."""
    db = _connect()
    students = {}
    for s in db.execute("SELECT key, name, class_name FROM students WHERE active = 1"):
        items = []
        for h in db.execute(
            "SELECT date, subject, content "
            "FROM homework WHERE student_key = ? AND deleted_at IS NULL "
            "ORDER BY substr(date,7,4)||substr(date,4,2)||substr(date,1,2) DESC "
            "LIMIT ?",
            (s["key"], n),
        ):
            items.append(dict(h))
        students[s["name"]] = {
            "class": s["class_name"],
            "homework": items,
        }
    db.close()
    return students


_EXAM_TYPE_LABELS = {1: "test", 2: "quiz"}


def _get_exams(
    student_filter: str | None = None,
    days_ahead: int = 21,
) -> dict[str, Any]:
    """Return upcoming exams per student, soonest first.

    Exam dates are stored as ISO 8601 timestamps (e.g. `2026-04-15T00:00:00+02:00`),
    so the date prefix is compared against today's `YYYY-MM-DD`.
    """
    db = _connect()
    result: dict[str, Any] = {}

    today = datetime.now().strftime("%Y-%m-%d")
    to_date = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    query = "SELECT key, name, class_name FROM students WHERE active = 1"
    params: tuple[str, ...] = ()
    if student_filter:
        query += " AND name = ?"
        params = (student_filter,)

    for s in db.execute(query, params):
        rows = db.execute(
            "SELECT date, subject, type, description, teacher "
            "FROM exams WHERE student_key = ? AND deleted_at IS NULL "
            "AND substr(date, 1, 10) >= ? AND substr(date, 1, 10) <= ? "
            "ORDER BY date ASC, subject ASC",
            (s["key"], today, to_date),
        ).fetchall()
        exams = [
            {
                "date": r["date"][:10],
                "subject": r["subject"],
                "type": _EXAM_TYPE_LABELS.get(r["type"], "exam"),
                "description": r["description"],
                "teacher": r["teacher"],
            }
            for r in rows
        ]
        result[s["name"]] = {
            "class": s["class_name"],
            "exams": exams,
            "count": len(exams),
        }

    db.close()
    return result


def _get_messages(n: int = 20) -> list[dict[str, Any]]:
    """Read latest N messages (unified inbox, not per-student)."""
    db = _connect()
    messages = []
    for m in db.execute(
        "SELECT sender, subject, date, mailbox, content FROM messages ORDER BY date DESC LIMIT ?",
        (n,),
    ):
        messages.append(dict(m))
    db.close()
    return messages


async def handle_grades_average(request: web.Request) -> web.Response:
    student = request.query.get("student")
    window = int(request.query.get("window", "30"))
    period = request.query.get("period")
    return _with_meta(_get_grade_averages(student, window, period), "grades")


async def handle_grades_monthly(request: web.Request) -> web.Response:
    student = request.query.get("student")
    year_q = request.query.get("year")
    year = int(year_q) if year_q else None
    months = int(request.query.get("months", "6"))
    return _with_meta(_get_monthly_averages(student, year, months), "grades")


def _get_lessons_for_ics(
    student_name: str, days_past: int, days_future: int
) -> tuple[str, list[dict[str, Any]]]:
    """Fetch all lessons (not only substitutions) for one student as a list of dicts.

    A student gets a fresh Vulcan key every school year (the key encodes the class
    register, not just the pupil), so one name can map to several keys. Union across
    all of them and let the date window drop the stale years.

    Deliberately does NOT filter on students.active, unlike the read paths above:
    `days_past` reaches back over a September rollover, where the lessons that
    belong in the feed still hang off last year's now-inactive key.

    Returns (student_key, lessons). Empty student_key if student not found.
    """
    db = _connect()
    key_rows = db.execute("SELECT key FROM students WHERE name = ?", (student_name,))
    keys = [r["key"] for r in key_rows]
    if not keys:
        db.close()
        return "", []

    today = datetime.now()
    date_from = (today - timedelta(days=days_past)).strftime("%Y-%m-%d")
    date_to = (today + timedelta(days=days_future)).strftime("%Y-%m-%d")

    placeholders = ",".join("?" * len(keys))
    rows = db.execute(
        "SELECT student_key, date, time_from, time_to, subject, teacher, room, group_name, "
        "annotation, is_extra, sub_teacher, sub_room, sub_type, absence_info, remarks, "
        "first_seen, last_seen "
        f"FROM schedule WHERE student_key IN ({placeholders}) AND date >= ? AND date <= ? "
        "ORDER BY date ASC, time_from ASC",
        (*keys, date_from, date_to),
    ).fetchall()
    db.close()

    lessons = [
        {
            "student_key": r["student_key"],
            "date": r["date"],
            "time_from": r["time_from"],
            "time_to": r["time_to"],
            "subject": r["subject"],
            "teacher": r["teacher"],
            "room": r["room"],
            "group_name": r["group_name"],
            "annotation": r["annotation"],
            "is_extra": bool(r["is_extra"]),
            "sub_teacher": r["sub_teacher"],
            "sub_room": r["sub_room"],
            "sub_type": r["sub_type"],
            "absence_info": r["absence_info"],
            "remarks": r["remarks"],
            # Carried through so each VEVENT gets a DTSTAMP reflecting when the
            # lesson last changed rather than when the feed was served.
            "first_seen": r["first_seen"],
            "last_seen": r["last_seen"],
        }
        for r in rows
    ]
    return keys[0], lessons


async def handle_calendar(request: web.Request) -> web.Response:
    """Serve an RFC 5545 iCalendar feed for one student's schedule."""
    from vulcan_notify.ics import build_calendar

    student_name = request.match_info["student"]
    # URL path may be URL-encoded (space -> %20); aiohttp decodes match_info already.
    days_past = int(request.query.get("past", "30"))
    days_future = int(request.query.get("future", "60"))

    key, lessons = _get_lessons_for_ics(student_name, days_past, days_future)
    if not key:
        return web.Response(status=404, text=f"Unknown student: {student_name}")

    # A frozen feed has to announce itself in the calendar; see ics._stale_event.
    schedule_health = _get_health().get("sections", {}).get("schedule", {})
    is_stale = bool(schedule_health.get("stale"))
    age = schedule_health.get("age_seconds")
    stale_since = datetime.now(UTC) - timedelta(seconds=age) if age is not None else None

    body = build_calendar(
        student_name, lessons, key, stale=is_stale, stale_since=stale_since
    )
    return web.Response(
        body=body.encode("utf-8"),
        content_type="text/calendar",
        charset="utf-8",
        headers={
            "Cache-Control": "public, max-age=900",
            "Content-Disposition": f'inline; filename="{student_name}.ics"',
        },
    )


def _with_meta(payload: dict[str, Any], *sections: str) -> web.Response:
    """Attach provenance and reply. `_meta` sorts before student names on purpose."""
    return web.json_response({"_meta": _meta(*sections), **payload})


async def handle_schedule(request: web.Request) -> web.Response:
    student = request.query.get("student")
    only_subs = request.query.get("only_substitutions", "").lower() in ("1", "true", "yes")
    days = int(request.query.get("days", "14"))
    return _with_meta(_get_schedule(student, only_subs, days), "schedule")


async def handle_grades_by_subject(request: web.Request) -> web.Response:
    student = request.query.get("student")
    period = request.query.get("period")
    return _with_meta(_get_subject_averages(student, period), "grades")


async def handle_grades_summary(request: web.Request) -> web.Response:
    student = request.query.get("student")
    period = request.query.get("period")
    return _with_meta(_get_subject_summaries(student, period), "grades")


async def handle_grades(request: web.Request) -> web.Response:
    n = int(request.query.get("n", "5"))
    return _with_meta(_get_grades(n), "grades")


async def handle_homework(request: web.Request) -> web.Response:
    n = int(request.query.get("n", "5"))
    return _with_meta(_get_homework(n), "homework")


async def handle_messages(request: web.Request) -> web.Response:
    n = int(request.query.get("n", "20"))
    return _with_meta({"messages": _get_messages(n)}, "messages")


async def handle_exams(request: web.Request) -> web.Response:
    student = request.query.get("student")
    days = int(request.query.get("days", "21"))
    return _with_meta(_get_exams(student, days), "exams")


async def handle_alive(request: web.Request) -> web.Response:
    """Pure liveness: is this process serving HTTP at all?

    Kept separate from /api/health so the Docker healthcheck doesn't mark the
    container unhealthy just because Vulcan upstream is down.
    """
    return web.json_response({"alive": True})


async def handle_health(request: web.Request) -> web.Response:
    """Report whether the data behind this API is actually current.

    Returns 503 when stale or failed so that dumb HTTP probes -- pve-healthcheck on
    the PVE host -- can detect a frozen pipeline without understanding the payload.
    This endpoint used to be a static {"status": "ok"} literal, which meant every
    watchdog above it was decorative.

    `?soft=1` always returns 200. Home Assistant's REST sensor marks an entity
    unavailable on any non-2xx, which would throw away this payload at exactly the
    moment it becomes interesting, so HA reads the soft variant and decides for
    itself based on `status`.
    """
    health = _get_health()
    soft = request.query.get("soft", "").lower() in ("1", "true", "yes")
    code = 503 if not soft and health["status"] in ("stale", "failed") else 200
    return web.json_response(health, status=code)


def create_app() -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    app.router.add_get("/api/grades/average", handle_grades_average)
    app.router.add_get("/api/grades/monthly", handle_grades_monthly)
    app.router.add_get("/api/grades/by-subject", handle_grades_by_subject)
    app.router.add_get("/api/grades/summary", handle_grades_summary)
    app.router.add_get("/api/schedule", handle_schedule)
    app.router.add_get("/calendar/{student}.ics", handle_calendar)
    app.router.add_get("/api/grades", handle_grades)
    app.router.add_get("/api/homework", handle_homework)
    app.router.add_get("/api/messages", handle_messages)
    app.router.add_get("/api/exams", handle_exams)
    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/alive", handle_alive)
    return app


def run_api(port: int = 8585, access_log: bool = False) -> None:
    app = create_app()
    logger.info("Starting API server on port %d", port)
    # Access logging is off by default. HA polls 8 resources every 5 minutes against a
    # sync that runs every 30, so access records were ~99% of the container log and
    # buried every real error. Set API_ACCESS_LOG=1 to get them back for debugging.
    web.run_app(
        app,
        host="0.0.0.0",
        port=port,
        print=None,
        access_log=logging.getLogger("aiohttp.access") if access_log else None,
    )


if __name__ == "__main__":
    import os

    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper()),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    run_api(
        port=int(os.environ.get("API_PORT", "8585")),
        access_log=os.environ.get("API_ACCESS_LOG", "").lower() in ("1", "true", "yes"),
    )
