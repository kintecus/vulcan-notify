# Implementation spec: vulcan-notify - Phase 1

**Contract**: ./contract.md
**Estimated effort**: M

## Phase overview

Replace the iris library dependency with a direct HTTP client for the eduVulcan web API. Design and implement a proper SQLite schema that can store all tracked data types (grades, attendance, exams, homework, messages, student context). This is the foundation everything else builds on.

## Technical approach

Build a `VulcanClient` class that wraps aiohttp and handles cookie-based auth, session validation, and all API calls documented in `docs/eduvulcan-api.md`. The client returns typed dataclasses (not raw dicts) for each endpoint.

Redesign the SQLite schema from the current hash-only `seen_items` table to full normalized tables that store actual data (not just hashes). This enables the CLI to display meaningful information and supports future AI summary features. Keep aiosqlite as the async wrapper.

Remove iris from dependencies since it's no longer used at runtime.

## Feedback strategy

**Inner-loop command**: `uv run pytest tests/ -x -q`

**Playground**: Test suite with mocked HTTP responses. The API client and database are pure data layers - tests are the fastest feedback loop.

**Why this approach**: Every component is data-in/data-out with no UI. Tests catch regressions instantly and run in milliseconds.

## File changes

### New files

| File path | Purpose |
|-----------|---------|
| `src/vulcan_notify/client.py` | HTTP client for eduVulcan web API |
| `src/vulcan_notify/models.py` | Dataclasses for all API response types |
| `tests/test_client.py` | Tests for API client with mocked responses |
| `tests/test_db.py` | Tests for new database schema and operations |

### Modified files

| File path | Changes |
|-----------|---------|
| `src/vulcan_notify/db.py` | New schema with normalized tables; migration from old schema |
| `src/vulcan_notify/config.py` | Add `message_sender_whitelist` setting |
| `pyproject.toml` | Remove iris dependency |

### Deleted files

| File path | Reason |
|-----------|--------|
| None | Poller and differ will be updated in Phase 2 |

## Implementation details

### Models (`models.py`)

**Overview**: Typed dataclasses matching the eduVulcan API response shapes. Used by both the client and database layers.

```python
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class Student:
    key: str
    name: str  # uczen
    class_name: str  # oddzial
    school: str  # jednostka
    diary_id: int  # idDziennik
    mailbox_key: str  # globalKeySkrzynka

@dataclass
class ClassificationPeriod:
    id: int
    number: int  # numerOkresu
    date_from: datetime
    date_to: datetime

@dataclass
class Grade:
    column_id: int  # idKolumny
    value: str  # wpis
    date: str  # dataOceny
    subject: str  # from parent Subject
    column_name: str  # nazwaKolumny
    category: str  # kategoriaKolumny
    weight: int  # waga
    teacher: str  # nauczyciel
    changed_since_login: bool  # zmienionaOdOstatniegoLogowania

@dataclass
class AttendanceEntry:
    lesson_number: int  # numerLekcji
    category: int  # kategoriaFrekwencji
    date: datetime  # data
    subject: str  # opisZajec
    teacher: str  # nauczyciel
    time_from: datetime  # godzinaOd
    time_to: datetime  # godzinaDo

@dataclass
class Exam:
    id: int
    date: datetime  # data
    subject: str  # przedmiot
    type: int  # rodzaj

@dataclass
class Homework:
    id: int
    date: datetime  # data
    subject: str  # przedmiot

@dataclass
class Message:
    sender: str
    subject: str
    date: datetime
    content: str | None = None

@dataclass
class DashboardData:
    """Combined response from all Tablica endpoints for one student."""
    grades: list  # raw OcenyTablica response
    attendance: dict  # raw FrekwencjaTablica response
    exams: list[Exam] = field(default_factory=list)
    homework: list[Homework] = field(default_factory=list)
    announcements: list = field(default_factory=list)
    unread_messages: int = 0
```

**Key decisions**:
- Use dataclasses (not Pydantic) to avoid heavy dependency for simple data containers
- Field names are English translations of Polish API names, with comments mapping back
- `DashboardData` groups all Tablica responses for a single student sync

### API Client (`client.py`)

**Pattern to follow**: `src/vulcan_notify/auth.py` (cookie handling, SSL context)

**Overview**: Async HTTP client that wraps all eduVulcan API endpoints. Handles cookie auth, session expiry detection, and response parsing.

```python
class SessionExpiredError(Exception):
    """Raised when the session cookies are no longer valid."""

class VulcanClient:
    def __init__(self, session_data: dict) -> None: ...

    async def get_students(self) -> list[Student]: ...
    async def get_periods(self, student: Student) -> list[ClassificationPeriod]: ...
    async def get_grades(self, student: Student, period: ClassificationPeriod) -> list[Grade]: ...
    async def get_attendance(self, student: Student, date_from: datetime, date_to: datetime) -> list[AttendanceEntry]: ...
    async def get_dashboard(self, student: Student) -> DashboardData: ...
    async def get_exams(self, student: Student) -> list[Exam]: ...
    async def get_homework(self, student: Student) -> list[Homework]: ...
```

**Key decisions**:
- Single `aiohttp.ClientSession` per client instance, reused across calls
- `_request()` private method handles cookie header, SSL context (certifi), and session expiry detection (HTML response = expired)
- `get_dashboard()` fetches all `*Tablica` endpoints concurrently with `asyncio.gather`
- All methods return typed dataclasses, not raw dicts

**Implementation steps**:
1. Create `_make_ssl_context()` (extract from auth.py) and `cookies_for_url()` into shared usage
2. Implement `_request()` with session expiry detection (check content-type for text/html)
3. Implement `get_students()` parsing `/api/Context`
4. Implement grade-related methods (periods + grades)
5. Implement attendance, exams, homework methods
6. Implement `get_dashboard()` that fetches all Tablica endpoints concurrently

**Feedback loop**:
- **Playground**: Create `tests/test_client.py` with a fixture that returns mocked API responses (based on real shapes from `docs/eduvulcan-api.md`)
- **Experiment**: Test each endpoint method with valid response, empty response, and HTML response (session expired)
- **Check command**: `uv run pytest tests/test_client.py -x -q`

### Database schema (`db.py`)

**Pattern to follow**: Current `src/vulcan_notify/db.py` (aiosqlite wrapper pattern)

**Overview**: Replace the hash-only `seen_items` table with normalized tables that store actual data. Keep the `Database` class pattern but expand it.

```sql
-- Students from /api/Context
CREATE TABLE IF NOT EXISTS students (
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    class_name TEXT NOT NULL,
    school TEXT NOT NULL,
    diary_id INTEGER NOT NULL,
    mailbox_key TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Grades from /api/Oceny
CREATE TABLE IF NOT EXISTS grades (
    student_key TEXT NOT NULL,
    column_id INTEGER NOT NULL,
    value TEXT NOT NULL,
    date TEXT NOT NULL,
    subject TEXT NOT NULL,
    column_name TEXT NOT NULL,
    category TEXT NOT NULL,
    weight INTEGER DEFAULT 1,
    teacher TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (student_key, column_id),
    FOREIGN KEY (student_key) REFERENCES students(key)
);

-- Attendance from /api/Frekwencja
CREATE TABLE IF NOT EXISTS attendance (
    student_key TEXT NOT NULL,
    date TEXT NOT NULL,
    lesson_number INTEGER NOT NULL,
    category INTEGER NOT NULL,
    subject TEXT NOT NULL,
    teacher TEXT,
    time_from TEXT,
    time_to TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (student_key, date, lesson_number),
    FOREIGN KEY (student_key) REFERENCES students(key)
);

-- Exams from /api/SprawdzianyTablica
CREATE TABLE IF NOT EXISTS exams (
    id INTEGER PRIMARY KEY,
    student_key TEXT NOT NULL,
    date TEXT NOT NULL,
    subject TEXT NOT NULL,
    type INTEGER NOT NULL,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_key) REFERENCES students(key)
);

-- Homework from /api/ZadaniaDomoweTablica
CREATE TABLE IF NOT EXISTS homework (
    id INTEGER PRIMARY KEY,
    student_key TEXT NOT NULL,
    date TEXT NOT NULL,
    subject TEXT NOT NULL,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_key) REFERENCES students(key)
);

-- Messages (store all, filter on display)
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    student_key TEXT,
    sender TEXT NOT NULL,
    subject TEXT NOT NULL,
    date TEXT,
    content TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Sync state tracking
CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Key decisions**:
- Store full data, not just hashes - enables display and future AI features
- Messages store all senders; whitelist filtering happens at display time
- `sync_state` replaces `poll_state` - tracks last sync timestamp per student
- Keep `first_seen` on all tables for "new since when" queries
- Composite primary keys match the natural dedup keys from the API

**Implementation steps**:
1. Define new schema as string constant
2. Add `upsert_*` methods for each table (INSERT OR REPLACE pattern)
3. Add `get_*_since(timestamp)` methods for change queries
4. Add schema migration: detect old `seen_items` table and drop it
5. Keep `Database.connect()` / `Database.close()` pattern

**Feedback loop**:
- **Playground**: Create `tests/test_db.py` with the `db` fixture from conftest.py
- **Experiment**: Test upsert idempotency (insert same grade twice = one row), test `get_since` with various timestamps, test schema migration from old to new
- **Check command**: `uv run pytest tests/test_db.py -x -q`

### Config changes (`config.py`)

Add message sender whitelist:

```python
# Message filtering
message_sender_whitelist: list[str] = []  # Empty = show all
```

Configured via `MESSAGE_SENDER_WHITELIST=Teacher Name 1,Teacher Name 2` in `.env`.

## Testing requirements

### Unit tests

| Test file | Coverage |
|-----------|----------|
| `tests/test_client.py` | All API methods, session expiry detection, empty responses |
| `tests/test_db.py` | Schema creation, upserts, dedup, get_since queries, migration |

**Key test cases**:
- Client returns typed dataclasses from mocked JSON responses
- Client raises `SessionExpiredError` when response is HTML
- Client handles empty arrays gracefully
- Database upsert is idempotent (same data = no duplicate rows)
- Database `get_since` returns only records after given timestamp
- Old schema is migrated cleanly

### Manual testing

- [ ] `uv run vulcan-notify auth` still works (unchanged)
- [ ] `uv run vulcan-notify test` validates session using new client

## Error handling

| Error scenario | Handling strategy |
|----------------|-------------------|
| Session expired (HTML response) | Raise `SessionExpiredError` with message to re-run auth |
| Network error | Let aiohttp exception propagate with context |
| Empty API response | Return empty list/default values, don't error |
| Malformed JSON | Log warning, skip item, continue with others |

## Validation commands

```bash
# Type checking
uv run mypy src/

# Linting
uv run ruff check .

# Tests
uv run pytest tests/ -x -q

# Single test file
uv run pytest tests/test_client.py -x -q
```

## Open items

- [ ] Determine exact `kategoriaFrekwencji` mapping (need to observe more values during real syncs)
- [ ] Decide if messages need a detail endpoint or if the summary is sufficient for v1
- [ ] Session cookie TTL - how long before re-auth is needed? (will learn from usage)

---

_This spec is ready for implementation. Follow the patterns and validate at each step._
