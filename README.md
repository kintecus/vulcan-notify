# vulcan-notify

CLI tool that syncs data from the eduVulcan school e-journal to a local SQLite database and shows what changed since the last sync.

Solves the problem of eduVulcan paywalling push notifications behind a subscription, while the web version (which is legally required to remain free) has no notification support.

## What it tracks

- **Grades** - new and changed, with subject, teacher, and category
- **Attendance** - absences, late arrivals
- **Exams** - upcoming tests and quizzes
- **Homework** - upcoming assignments
- **Messages** - unread count, with optional sender whitelist filtering

Supports multiple students under one parent account.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager

## Setup

```bash
# Clone and install
git clone https://github.com/yourname/vulcan-notify.git
cd vulcan-notify
uv sync

# Install Playwright browsers (needed for auth)
uv run playwright install chromium

# Configure (optional)
cp .env.example .env
# Edit .env to set MESSAGE_SENDER_WHITELIST if desired

# Authenticate with eduVulcan (opens browser)
uv run vulcan-notify auth

# Test session validity
uv run vulcan-notify test

# Sync data and see changes
uv run vulcan-notify sync
```

## Commands

| Command | Description |
|---------|-------------|
| `vulcan-notify auth` | Interactive browser login, saves session cookies |
| `vulcan-notify test` | Test if saved session is still valid |
| `vulcan-notify sync` | Fetch latest data and show changes (default) |

## How it works

1. **Auth**: Playwright opens a browser for you to log into eduvulcan.pl. After login, session cookies are saved locally.

2. **Sync**: The tool calls the eduVulcan web API directly (using saved cookies) to fetch grades, attendance, exams, and homework for all students.

3. **Diff**: Each item is compared against the local SQLite database. New or changed items are reported.

4. **Display**: Changes are printed to the terminal, grouped by student and data type.

On first sync, all existing data is stored without reporting changes (baseline). Only subsequent syncs show what's new.

## Configuration

All settings are via environment variables or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | `vulcan_notify.db` | SQLite database path |
| `SESSION_FILE` | `session.json` | Saved session cookies path |
| `MESSAGE_SENDER_WHITELIST` | (empty) | Comma-separated sender names to filter messages |
| `LOG_LEVEL` | `INFO` | Logging level |
