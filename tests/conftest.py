"""Shared test fixtures."""

from pathlib import Path

import pytest

from vulcan_notify.db import Database


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    """Create a temporary test database."""
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database  # type: ignore[misc]
    await database.close()
