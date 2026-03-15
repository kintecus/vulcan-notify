# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Polling service that detects changes in Vulcan/eduVulcan school e-journal (grades, messages, attendance, announcements) and sends push notifications via ntfy.sh. Built on top of the [iris](https://github.com/bbrjpl1310b/iris) library for eduVulcan API access.

## Commands

```bash
# Install dependencies
uv sync

# Run the service
uv run vulcan-notify run

# Run all tests
uv run pytest

# Run a single test
uv run pytest tests/test_differ.py::test_new_grade_detected

# Lint and format
uv run ruff check --fix .
uv run ruff format .

# Type checking
uv run mypy src/

# Pre-commit hooks (ruff check + format)
uv run pre-commit run --all-files
```

## Architecture

The service follows a linear pipeline: **Auth -> Poll -> Diff -> Notify**.

- `auth.py` - Playwright-based browser login flow. Intercepts JWT tokens from the eduVulcan auth redirect, then registers an RSA keypair as a mobile device via iris. Credentials are serialized to `credential.json` as `RsaCredential` (pydantic model from iris).

- `poller.py` - `Poller` class runs an async loop on a configurable interval. Uses `IrisHebeCeApi` (from iris) to fetch grades, messages, attendance, and announcements. On first run, populates baseline state without sending notifications (tracked via `poll_state` table).

- `differ.py` - Stateless change detection. Each `detect_*_changes()` function takes API response objects and a `Database`, hashes each item with `_hash_dict()` (SHA-256, truncated to 16 chars), and compares against stored hashes. Returns `Change` dataclasses with ntfy-ready fields (title, body, priority, tags).

- `db.py` - `Database` class wrapping aiosqlite. Two tables: `seen_items` (item_type + item_id composite PK, stores hashes for dedup) and `poll_state` (key-value for service state like "initialized"). All writes commit immediately.

- `notifier.py` - Single `send_notification()` function that POSTs to ntfy.sh via aiohttp. Uses ntfy headers (Title, Priority, Tags, Click) rather than JSON body.

- `config.py` - `pydantic-settings` `Settings` singleton loaded from `.env`. All config is via environment variables.

## Key dependencies

- **iris** - eduVulcan API client (installed from git). Provides `IrisHebeCeApi` and `RsaCredential`. API objects have `.model_dump()` for serialization.
- **playwright** - only used in auth flow, not at runtime
- **aiohttp** - HTTP client for ntfy.sh
- **aiosqlite** - async SQLite for state storage

## Testing patterns

- Tests use `pytest-asyncio` with `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed)
- The `db` fixture in `conftest.py` provides a temporary SQLite database
- API objects are faked with simple classes that implement `model_dump()` - no iris dependency needed in tests
- aiohttp is mocked with nested async context manager mocks (see `_make_mock_session` in `test_notifier.py`)
