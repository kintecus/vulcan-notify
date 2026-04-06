"""Tiny HTTP API for Home Assistant integration."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from aiohttp import web

from vulcan_notify.config import settings

logger = logging.getLogger(__name__)


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(str(settings.db_path))
    db.row_factory = sqlite3.Row
    return db


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


def _get_grade_averages(student_filter: str | None = None) -> dict[str, Any]:
    """Compute weighted grade averages per student, with cumulative time series."""
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

        weighted_sum = 0.0
        weight_sum = 0
        count = 0
        timeline: list[dict[str, Any]] = []

        for g in grades:
            numeric = _grade_to_numeric(g["value"])
            if numeric is None:
                continue
            w = g["weight"] or 1
            weighted_sum += numeric * w
            weight_sum += w
            count += 1
            # Convert DD.MM.YYYY to YYYY-MM-DD for HA
            raw_date = g["date"]
            iso_date = f"{raw_date[6:10]}-{raw_date[3:5]}-{raw_date[0:2]}"
            timeline.append({
                "date": iso_date,
                "cumulative_average": round(weighted_sum / weight_sum, 2),
            })

        result[s["name"]] = {
            "average": round(weighted_sum / weight_sum, 2) if weight_sum else None,
            "count": count,
            "grades_over_time": timeline,
        }

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
        "SELECT sender, subject, date, content "
        "FROM messages ORDER BY date DESC LIMIT ?",
        (n,),
    ):
        messages.append(dict(m))
    db.close()
    return messages


async def handle_grades_average(request: web.Request) -> web.Response:
    student = request.query.get("student")
    return web.json_response(_get_grade_averages(student))


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
