"""Tiny HTTP API for Home Assistant integration."""

from __future__ import annotations

import calendar
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from aiohttp import web

from vulcan_notify.config import settings

logger = logging.getLogger(__name__)


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(str(settings.db_path))
    db.row_factory = sqlite3.Row
    return db


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

    query = "SELECT key, name FROM students"
    params: tuple[str, ...] = ()
    if student_filter:
        query += " WHERE name = ?"
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

    query = "SELECT key, name FROM students"
    params: tuple[str, ...] = ()
    if student_filter:
        query += " WHERE name = ?"
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

    query = "SELECT key, name FROM students"
    params: tuple[str, ...] = ()
    if student_filter:
        query += " WHERE name = ?"
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

    query = "SELECT key, name FROM students"
    params: tuple[str, ...] = ()
    if student_filter:
        query += " WHERE name = ?"
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
    for s in db.execute("SELECT key, name, class_name FROM students"):
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
    for s in db.execute("SELECT key, name, class_name FROM students"):
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

    query = "SELECT key, name, class_name FROM students"
    params: tuple[str, ...] = ()
    if student_filter:
        query += " WHERE name = ?"
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
    return web.json_response(_get_grade_averages(student, window, period))


async def handle_grades_monthly(request: web.Request) -> web.Response:
    student = request.query.get("student")
    year_q = request.query.get("year")
    year = int(year_q) if year_q else None
    months = int(request.query.get("months", "6"))
    return web.json_response(_get_monthly_averages(student, year, months))


def _get_lessons_for_ics(
    student_name: str, days_past: int, days_future: int
) -> tuple[str, list[dict[str, Any]]]:
    """Fetch all lessons (not only substitutions) for one student as a list of dicts.

    Returns (student_key, lessons). Empty student_key if student not found.
    """
    db = _connect()
    student_row = db.execute("SELECT key FROM students WHERE name = ?", (student_name,)).fetchone()
    if not student_row:
        db.close()
        return "", []

    today = datetime.now()
    date_from = (today - timedelta(days=days_past)).strftime("%Y-%m-%d")
    date_to = (today + timedelta(days=days_future)).strftime("%Y-%m-%d")

    rows = db.execute(
        "SELECT date, time_from, time_to, subject, teacher, room, group_name, "
        "annotation, is_extra, sub_teacher, sub_room, sub_type, absence_info, remarks "
        "FROM schedule WHERE student_key = ? AND date >= ? AND date <= ? "
        "ORDER BY date ASC, time_from ASC",
        (student_row["key"], date_from, date_to),
    ).fetchall()
    key = student_row["key"]
    db.close()

    lessons = [
        {
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
        }
        for r in rows
    ]
    return key, lessons


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

    body = build_calendar(student_name, lessons, key)
    return web.Response(
        body=body.encode("utf-8"),
        content_type="text/calendar",
        charset="utf-8",
        headers={
            "Cache-Control": "public, max-age=900",
            "Content-Disposition": f'inline; filename="{student_name}.ics"',
        },
    )


async def handle_schedule(request: web.Request) -> web.Response:
    student = request.query.get("student")
    only_subs = request.query.get("only_substitutions", "").lower() in ("1", "true", "yes")
    days = int(request.query.get("days", "14"))
    return web.json_response(_get_schedule(student, only_subs, days))


async def handle_grades_by_subject(request: web.Request) -> web.Response:
    student = request.query.get("student")
    period = request.query.get("period")
    return web.json_response(_get_subject_averages(student, period))


async def handle_grades(request: web.Request) -> web.Response:
    n = int(request.query.get("n", "5"))
    return web.json_response(_get_grades(n))


async def handle_homework(request: web.Request) -> web.Response:
    n = int(request.query.get("n", "5"))
    return web.json_response(_get_homework(n))


async def handle_messages(request: web.Request) -> web.Response:
    n = int(request.query.get("n", "20"))
    return web.json_response({"messages": _get_messages(n)})


async def handle_exams(request: web.Request) -> web.Response:
    student = request.query.get("student")
    days = int(request.query.get("days", "21"))
    return web.json_response(_get_exams(student, days))


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def handle_practice_index(request: web.Request) -> web.Response:
    """Serve the practice webapp index. /practice -> /practice/ redirect handled here too."""
    static_root = Path(__file__).parent / "static" / "practice"
    return web.FileResponse(static_root / "index.html")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/grades/average", handle_grades_average)
    app.router.add_get("/api/grades/monthly", handle_grades_monthly)
    app.router.add_get("/api/grades/by-subject", handle_grades_by_subject)
    app.router.add_get("/api/schedule", handle_schedule)
    app.router.add_get("/calendar/{student}.ics", handle_calendar)
    app.router.add_get("/api/grades", handle_grades)
    app.router.add_get("/api/homework", handle_homework)
    app.router.add_get("/api/messages", handle_messages)
    app.router.add_get("/api/exams", handle_exams)
    app.router.add_get("/api/health", handle_health)

    # Kids practice webapp — vanilla JS SPA, content as static JSON files.
    static_root = Path(__file__).parent / "static" / "practice"
    if static_root.exists():
        app.router.add_get("/practice", handle_practice_index)
        app.router.add_get("/practice/", handle_practice_index)
        app.router.add_static("/practice/", static_root, show_index=False)
    return app


def run_api(port: int = 8585) -> None:
    app = create_app()
    logger.info("Starting API server on port %d", port)
    web.run_app(app, host="0.0.0.0", port=port, print=None)


if __name__ == "__main__":
    import os

    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper()),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    run_api(port=int(os.environ.get("API_PORT", "8585")))
