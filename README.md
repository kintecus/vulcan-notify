# vulcan-notify

CLI tool that syncs data from the eduVulcan school e-journal to a local SQLite database and shows what changed since the last sync.

Solves the problem of eduVulcan paywalling push notifications behind a subscription, while the web version (which is legally required to remain free) has no notification support.

## What it tracks

- **Grades** - new and changed, with subject, teacher, and category
- **Attendance** - absences, late arrivals
- **Exams** - upcoming tests and quizzes, with description and teacher
- **Homework** - upcoming assignments, with full description and teacher
- **Messages** - unread count, with optional sender whitelist filtering
- **iCloud Calendar** - pushes exams and homework to macOS Calendar (optional)

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
| `vulcan-notify calendar` | Force re-sync all exams/homework to macOS Calendar |
| `vulcan-notify summarize` | AI summary of stored messages (requires `LLM_API_KEY`) |

## How it works

1. **Auth**: Playwright opens a browser for you to log into eduvulcan.pl. After login, session cookies are saved locally.

2. **Sync**: The tool calls the eduVulcan web API directly (using saved cookies) to fetch grades (all periods), attendance (last 90 days), exams, homework (with full descriptions), and messages for all students. Each sync run is tracked in the database.

3. **Diff**: Each item is compared against the local SQLite database. New or changed items are reported. Exams and homework that disappear from the API are soft-deleted.

4. **Calendar**: If configured, new and updated exams/homework are pushed to macOS Calendar as all-day events with reminder alarms.

5. **Display**: Changes are printed to the terminal, grouped by student and data type. Optionally, an AI-generated summary is appended.

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

## Calendar integration (macOS)

Push exams and homework to iCloud Calendar as all-day events with reminder alarms. Events sync to all devices via iCloud.

Add to `.env`:

```
CALENDAR_MAP={"Yarema Senyuk": "School Yarema", "Solomiia Senyuk": "School Solya"}
```

The calendar names must match existing calendars in macOS Calendar. Each student maps to their own calendar.

When configured, `vulcan-notify sync` automatically creates and updates calendar events. Use `vulcan-notify calendar` to force a clean re-sync of all events.

Events are deduplicated by storing the macOS calendar UID in the database. When exams or homework are removed from the API (soft-deleted), their calendar events are also removed.

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
  description : TEXT
  teacher : TEXT
  first_seen : TIMESTAMP
  last_seen : TIMESTAMP
  deleted_at : TIMESTAMP
  calendar_uid : TEXT
}

entity "homework" as homework #E8F5E9 {
  * **id** : INTEGER <<PK>>
  --
  student_key : TEXT <<FK>>
  date : TEXT
  subject : TEXT
  content : TEXT
  teacher : TEXT
  first_seen : TIMESTAMP
  last_seen : TIMESTAMP
  deleted_at : TIMESTAMP
  calendar_uid : TEXT
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

entity "sync_runs" as sync_runs #F5F5F5 {
  * **id** : INTEGER <<PK>>
  --
  started_at : TIMESTAMP
  completed_at : TIMESTAMP
  status : TEXT
  students_synced : INTEGER
  items_processed : INTEGER
  errors_count : INTEGER
  error_detail : TEXT
}

students ||--o{ grades
students ||--o{ attendance
students ||--o{ exams
students ||--o{ homework
@enduml
```

![Database schema](https://www.plantuml.com/plantuml/svg/lPRVJzim4CVVyrTOy5Qj3vE6n112C95ErKPXMv7sPbsJe_Ng7v7j32hO_yx5IOdJT2tR0q92kRCJt_TpvxkUEm_MbqecNdY9x18ypC0XSza25II9MmfTW0N5fD3eLmKoO_t290bgUcN53fmlStfs1mmSMnliC3qUVHXTiiU4iG4R39Qu6WpO2PkcFwVizFJcozaPhGo7z4-3mcQ5h4o2Sxphes2CaQsT2x0hBdBoZ2VJz7FwdPmAX9oP1qudjJlB8WUFEGTV-SPNwO_fnTLDygSDVsuXnphu-Z64VfH-V0czqSHx4jwnKIsZsfKPMIfDGOKzJLWRId-3B2DPLMYHo7Bs2pCVaQY_k867tfaR6qcyHp5V-0uAZq3fi-sUEs6TvmvHTp0mHh2t-2Cyu3tg77I60L5h_YUcIlEMGgYM93fdI6-fPcXtK8mGj99xz7eCl538xwnH6ovlzdAAUE03gBfQmbEFmixyHuXQ0WsSFSKGRbuic2eriwBmmkWTelynyTLd9MwvC1LrMMNUyZBSk_3zYCl2ABmtTXdGh8qtevCPJNMvA_jl1a9H5SEywIXhWnsEHgFZzFthG40X-5oQ6SWkYzl9-Djj6lOv2Y6MroFI1TRqnjQn04VAF45IeLsVi4_Nrr_JYmcj2SSjGjxnzG3llobkfJDEuyNNdQCr2SPHzVUQsR3HCVUtyt2CBRLh3wsitfbxAf66ujRS6rNyfImgQQMBCj9CGbx5WDrH9JmgnmjhCggFZJMqrbZ7CrDgtr_WENhAPLHtBnFtwMauD8_D4EkvsyRTMmgDhETTt-7adDwZ7mZF)

## Configuration

All settings are via environment variables or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | `vulcan_notify.db` | SQLite database path |
| `SESSION_FILE` | `session.json` | Saved session cookies path |
| `VULCAN_LOGIN` | (none) | eduVulcan login email for auto-login |
| `VULCAN_PASSWORD` | (none) | eduVulcan password for auto-login |
| `SYNC_ATTENDANCE_DAYS` | `90` | How many days back to sync attendance |
| `MESSAGE_SENDER_WHITELIST` | (empty) | Comma-separated sender names to filter messages |
| `CALENDAR_MAP` | (empty) | JSON dict mapping student names to macOS calendar names |
| `CALENDAR_REMINDER_HOURS` | `24` | Hours before event for calendar alarm |
| `LLM_BASE_URL` | `https://api.cerebras.ai/v1` | OpenAI-compatible API base URL for AI summaries |
| `LLM_API_KEY` | (none) | API key for AI summaries (disabled if unset) |
| `LLM_MODEL` | `qwen-3-235b-a22b-instruct-2507` | Model name for AI summaries |
| `LOG_LEVEL` | `INFO` | Logging level |
