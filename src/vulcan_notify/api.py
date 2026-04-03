"""Tiny HTTP API for Home Assistant integration."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from aiohttp import web

from vulcan_notify.config import settings

logger = logging.getLogger(__name__)


def _get_grades(n: int = 5) -> dict[str, Any]:
    """Read latest N grades per student from the database."""
    db = sqlite3.connect(str(settings.db_path))
    db.row_factory = sqlite3.Row

    students = {}
    for s in db.execute("SELECT key, name, class_name FROM students"):
        grades = []
        for g in db.execute(
            "SELECT value, date, subject, column_name, category "
            "FROM grades WHERE student_key = ? "
            "ORDER BY first_seen DESC LIMIT ?",
            (s["key"], n),
        ):
            grades.append(dict(g))
        students[s["name"]] = {
            "class": s["class_name"],
            "grades": grades,
        }
    db.close()
    return students


async def handle_grades(request: web.Request) -> web.Response:
    n = int(request.query.get("n", "5"))
    data = _get_grades(n)
    return web.json_response(data)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/grades", handle_grades)
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
