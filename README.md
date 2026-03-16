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
| `vulcan-notify summarize` | AI summary of stored messages (requires `LLM_API_KEY`) |

## How it works

1. **Auth**: Playwright opens a browser for you to log into eduvulcan.pl. After login, session cookies are saved locally.

2. **Sync**: The tool calls the eduVulcan web API directly (using saved cookies) to fetch grades, attendance, exams, homework, and messages for all students.

3. **Diff**: Each item is compared against the local SQLite database. New or changed items are reported.

4. **Display**: Changes are printed to the terminal, grouped by student and data type. Optionally, an AI-generated summary is appended.

On first sync, all existing data is stored without reporting changes (baseline). Only subsequent syncs show what's new.

## Auto-login (headless)

eduVulcan sessions expire after a few hours. To run fully unattended (e.g., on a home server with a cron job), you can store your credentials so the script re-authenticates automatically when a session expires.

**Option 1: macOS Keychain** (recommended, no plaintext on disk)

```bash
security add-generic-password -s vulcan-notify -a your.email@example.com -w
# Prompts for password interactively
```

**Option 2: environment variables**

Add to `.env`:

```
VULCAN_LOGIN=your.email@example.com
VULCAN_PASSWORD=your_password
```

When credentials are available, `vulcan-notify sync` detects expired sessions and re-authenticates headlessly via Playwright - no manual browser interaction needed.

## Database schema

```plantuml
@startuml
title Database schema

skinparam linetype ortho

entity "students" as students #E8F4FD {
  * **key** : TEXT <<PK>>
  --
  name : TEXT
  class_name : TEXT
  school : TEXT
  diary_id : INTEGER
  mailbox_key : TEXT
  updated_at : TIMESTAMP
}

entity "grades" as grades #E8F5E9 {
  * **student_key** : TEXT <<PK, FK>>
  * **column_id** : INTEGER <<PK>>
  --
  value : TEXT
  date : TEXT
  subject : TEXT
  column_name : TEXT
  category : TEXT
  weight : INTEGER
  teacher : TEXT
  first_seen : TIMESTAMP
  last_seen : TIMESTAMP
}

entity "attendance" as attendance #E8F5E9 {
  * **student_key** : TEXT <<PK, FK>>
  * **date** : TEXT <<PK>>
  * **lesson_number** : INTEGER <<PK>>
  --
  category : INTEGER
  subject : TEXT
  teacher : TEXT
  time_from : TEXT
  time_to : TEXT
  first_seen : TIMESTAMP
}

entity "exams" as exams #E8F5E9 {
  * **id** : INTEGER <<PK>>
  --
  student_key : TEXT <<FK>>
  date : TEXT
  subject : TEXT
  type : INTEGER
  first_seen : TIMESTAMP
}

entity "homework" as homework #E8F5E9 {
  * **id** : INTEGER <<PK>>
  --
  student_key : TEXT <<FK>>
  date : TEXT
  subject : TEXT
  first_seen : TIMESTAMP
}

entity "messages" as messages #FFF8E1 {
  * **id** : INTEGER <<PK>>
  --
  api_global_key : TEXT <<UNIQUE>>
  sender : TEXT
  subject : TEXT
  date : TEXT
  mailbox : TEXT
  has_attachments : BOOLEAN
  is_read : BOOLEAN
  content : TEXT
  first_seen : TIMESTAMP
}

entity "sync_state" as sync_state #F5F5F5 {
  * **key** : TEXT <<PK>>
  --
  value : TEXT
  updated_at : TIMESTAMP
}

students ||--o{ grades
students ||--o{ attendance
students ||--o{ exams
students ||--o{ homework
@enduml
```

![Database schema](https://www.plantuml.com/plantuml/svg/lLLDJzmm4BtdLrXmYxg7IaMY22509DMgPQ7j8lKMJP8XSTSVAzifHD3_tid74cS3BEsXYXHvtjYxxptFJ4wj0-CgAGB7dK1s0GvIiCXiLgA48B0hhjPWG3B15RfwZKmRL-eWG4L7QhPdNPNJskuni6mJiFteCFuGNx27WB6GXU4Awp1aHsmP_LYou-FhpoSdb9dDwAL0Of-XA1DWRJB6Y8pMOeXp3gPEU4x8VB6CFaNV29J0HQhl4_gdOMUrpi5Xde1hiFbbz7rvTdaT_1xe5mPoxCXtovRwGVJnYNglAPb8UCVYJaQpAzEYaef8jNjwMbjAVu6eF5aDDKzabVx4p7bETB-uPG-TARJn9DuXBqetii8XqFMPOSyjDzOb5b6DR62Cp7u6z-m1vr3be39iBHh2VxIfqVnC8JGfWTPgqbl95CqhBdeM398dxaqyS5nYSckqt8AStkcJvmVUW-ogfLrDN7Zr_ZsB1WTwStOKGzjvlk3TL4ijyKwRLSjs4_mtmhlvIRflAFhUsmHiFuxZm-Zzs_Z1cYU5q2c8CSMRnVphJTJkirIlVXbCY8vrz5Da04gmD3qS5PDi1ziHEx-w-XATBIZ7RM8GyX6MQKKjybT6s5fb2GrYr_NO498P1ytpbYcwDLjU7dnF8_hnSJRJ3_tKcy13fqzIRULFq4s51QTqZhueVm00)

## Configuration

All settings are via environment variables or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | `vulcan_notify.db` | SQLite database path |
| `SESSION_FILE` | `session.json` | Saved session cookies path |
| `VULCAN_LOGIN` | (none) | eduVulcan login email for auto-login |
| `VULCAN_PASSWORD` | (none) | eduVulcan password for auto-login |
| `MESSAGE_SENDER_WHITELIST` | (empty) | Comma-separated sender names to filter messages |
| `LLM_BASE_URL` | `https://api.cerebras.ai/v1` | OpenAI-compatible API base URL for AI summaries |
| `LLM_API_KEY` | (none) | API key for AI summaries (disabled if unset) |
| `LLM_MODEL` | `qwen-3-235b-a22b-instruct-2507` | Model name for AI summaries |
| `LOG_LEVEL` | `INFO` | Logging level |
