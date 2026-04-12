"""Tiny HTTP API for Home Assistant integration."""

from __future__ import annotations

import calendar
import logging
import sqlite3
from datetime import datetime, timedelta
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


def _grade_to_numeric(value: str) -> float | None:
    """Convert Polish grade string to numeric value. Returns None for non-gradeable marks."""
    v = value.strip().lower()
    if not v or v[0] not in "123456":
        return None
    base = int(v[0])
    if len(v) == 1 or v[1] == "p":
        return float(base)
    if v[1] == "+":
        return base + 0.5
    if v[1] == "-":
        return base - 0.25
    return float(base)


def _get_grade_averages(
    student_filter: str | None = None,
    window_days: int = 30,
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
        grades = db.execute(
            "SELECT value, date, weight FROM grades WHERE student_key = ? "
            "ORDER BY substr(date,7,4)||substr(date,4,2)||substr(date,1,2) ASC",
            (s["key"],),
        ).fetchall()

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
        grades = db.execute(
            "SELECT value, date, weight FROM grades WHERE student_key = ?",
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


def _get_subject_averages(student_filter: str | None = None) -> dict[str, Any]:
    """Compute weighted grade averages grouped by subject, sorted descending."""
    db = _connect()
    result: dict[str, Any] = {}

    query = "SELECT key, name FROM students"
    params: tuple[str, ...] = ()
    if student_filter:
        query += " WHERE name = ?"
        params = (student_filter,)

    for s in db.execute(query, params):
        grades = db.execute(
            "SELECT value, subject, weight FROM grades WHERE student_key = ?",
            (s["key"],),
        ).fetchall()

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


def _get_grades(n: int = 5) -> dict[str, Any]:
    """Read latest N grades per student from the database."""
    db = _connect()
    students = {}
    for s in db.execute("SELECT key, name, class_name FROM students"):
        grades = []
        for g in db.execute(
            "SELECT value, date, subject, column_name, category "
            "FROM grades WHERE student_key = ? "
            "ORDER BY substr(date,7,4)||substr(date,4,2)||substr(date,1,2) DESC "
            "LIMIT ?",
            (s["key"], n),
        ):
            grades.append(dict(g))
        students[s["name"]] = {
            "class": s["class_name"],
            "grades": grades,
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


def _get_messages(n: int = 20) -> list[dict[str, Any]]:
    """Read latest N messages (unified inbox, not per-student)."""
    db = _connect()
    messages = []
    for m in db.execute(
        "SELECT sender, subject, date, content FROM messages ORDER BY date DESC LIMIT ?",
        (n,),
    ):
        messages.append(dict(m))
    db.close()
    return messages


async def handle_grades_average(request: web.Request) -> web.Response:
    student = request.query.get("student")
    window = int(request.query.get("window", "30"))
    return web.json_response(_get_grade_averages(student, window))


async def handle_grades_monthly(request: web.Request) -> web.Response:
    student = request.query.get("student")
    year_q = request.query.get("year")
    year = int(year_q) if year_q else None
    months = int(request.query.get("months", "6"))
    return web.json_response(_get_monthly_averages(student, year, months))


async def handle_grades_by_subject(request: web.Request) -> web.Response:
    student = request.query.get("student")
    return web.json_response(_get_subject_averages(student))


async def handle_grades(request: web.Request) -> web.Response:
    n = int(request.query.get("n", "5"))
    return web.json_response(_get_grades(n))


async def handle_homework(request: web.Request) -> web.Response:
    n = int(request.query.get("n", "5"))
    return web.json_response(_get_homework(n))


async def handle_messages(request: web.Request) -> web.Response:
    n = int(request.query.get("n", "20"))
    return web.json_response({"messages": _get_messages(n)})


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/grades/average", handle_grades_average)
    app.router.add_get("/api/grades/monthly", handle_grades_monthly)
    app.router.add_get("/api/grades/by-subject", handle_grades_by_subject)
    app.router.add_get("/api/grades", handle_grades)
    app.router.add_get("/api/homework", handle_homework)
    app.router.add_get("/api/messages", handle_messages)
    app.router.add_get("/api/health", handle_health)
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
