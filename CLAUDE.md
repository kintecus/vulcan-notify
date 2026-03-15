# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CLI tool that syncs data from the eduVulcan school e-journal (grades, attendance, exams, homework, messages) to a local SQLite database and detects changes between syncs. Uses cookie-based auth via Playwright browser login.

## Commands

```bash
# Install dependencies
uv sync

# Authenticate (opens browser)
uv run vulcan-notify auth

# Test session validity
uv run vulcan-notify test

# Sync data and show changes (default command)
uv run vulcan-notify sync

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_sync.py -x -q

# Lint and format
uv run ruff check --fix .
uv run ruff format .

# Type checking
uv run mypy src/
```

## Architecture

The tool follows a linear pipeline: **Auth -> Client -> Sync -> Diff -> Display**.

- `auth.py` - Playwright-based browser login. Saves session cookies to `session.json` after user logs into eduvulcan.pl. Also provides `cookies_for_url()` and `_make_ssl_context()` used by the client.

- `client.py` - `VulcanClient` wraps aiohttp for the uczen.eduvulcan.pl JSON API. Handles cookie auth, SSL (certifi), and session expiry detection (HTML response = expired). Returns typed dataclasses from `models.py`.

- `models.py` - Dataclasses for all API response types: `Student`, `Grade`, `AttendanceEntry`, `Exam`, `Homework`, `ClassificationPeriod`, `DashboardData`.

- `sync.py` - `sync_all()` orchestrates per-student sync: fetch data via client, diff against stored state, upsert into database. Returns `SyncResult` per student. First sync stores baseline without reporting changes.

- `differ.py` - Compares fetched API data against stored database rows. `diff_grades()` detects new/updated grades by column_id. `diff_attendance()` detects new records by (date, lesson_number). Returns `Change` dataclasses.

- `display.py` - Formats `SyncResult` for terminal output with ANSI colors (auto-disabled when piped). Groups by student, then by data type.

- `db.py` - `Database` class wrapping aiosqlite. Normalized tables: students, grades, attendance, exams, homework, messages, sync_state. All writes use INSERT OR REPLACE for idempotent upserts.

- `config.py` - `pydantic-settings` `Settings` singleton loaded from `.env`.

## Key dependencies

- **playwright** - browser automation for auth flow only
- **aiohttp** - HTTP client for eduVulcan API
- **aiosqlite** - async SQLite storage
- **certifi** - CA certificates for SSL
- **pydantic-settings** - config from environment variables

## Testing patterns

- Tests use `pytest-asyncio` with `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed)
- The `db` fixture in `conftest.py` provides a temporary SQLite database
- `VulcanClient` is mocked with `AsyncMock` in sync tests
- aiohttp is mocked with `MagicMock` in client tests (see `_mock_response` pattern in `test_client.py`)

## API reference

See `docs/eduvulcan-api.md` for the reverse-engineered eduVulcan web API documentation.
