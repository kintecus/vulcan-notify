# iCal calendar integration

## Context

Parents need exam and homework dates visible in their shared family calendars. The tool already syncs this data from eduVulcan to a local SQLite database. This feature pushes exams and homework to macOS Calendar (which syncs to iCloud automatically), using AppleScript - no credentials or new dependencies needed.

## Approach

AppleScript via `asyncio.create_subprocess_exec("osascript", "-e", ...)`. macOS Calendar handles iCloud sync transparently. This is macOS-only, suitable for the current PoC. Using async subprocess to stay consistent with the async codebase.

## Calendar mapping

Each student maps to an existing iCloud calendar:

| Student | Calendar |
|---------|----------|
| Alice Smith | School Alice |
| Bob Johnson | School Bob |

Configured via `CALENDAR_MAP` in `config.py` as a JSON-encoded dict in `.env`:
```
CALENDAR_MAP={"Alice Smith": "School Alice", "Bob Johnson": "School Bob"}
```

The calendar module joins against the `students` table to resolve `student_key -> student.name -> calendar_name`. If a student has no mapping, their events are skipped with a warning.

Calendar sync is opt-in: if `CALENDAR_MAP` is empty (default), no calendar operations happen.

## Event format

- **Type**: all-day events on the due date
- **Title**: `{type} - {subject}` where type is "Kartkowka" (quiz), "Sprawdzian" (test), or "Zadanie domowe" (homework)
  - Exam type mapping: `1 -> "Sprawdzian"`, `2 -> "Kartkowka"`, fallback `"Sprawdzian/kartkowka"`
- **Body**: description/content text + "Nauczyciel: {teacher}" on a separate line (if available)
- **Alarm**: 1 day before (configurable via `CALENDAR_REMINDER_HOURS`, default 24)
- **Date handling**: ISO 8601 strings from the DB (e.g., `2026-03-25T00:00:00+01:00`) are parsed to `datetime.date` and formatted for AppleScript as `date "YYYY-MM-DD"` using the international date format to avoid locale issues

## Deduplication and updates

- New `calendar_uid TEXT` column on both `exams` and `homework` tables
- When creating an event, store the returned AppleScript UID in this column
- On every sync: always update events that have a `calendar_uid` with current data from the DB (date, title, description). This ensures changes in Vulcan (rescheduled exam, updated homework description) are reflected in the calendar without needing change-detection logic.
- Items without `calendar_uid` get created as new events
- Migration: `ALTER TABLE ADD COLUMN` (same pattern as other migrations, added after existing exam/homework migration blocks)
- AppleScript UIDs are local identifiers that can become stale if the calendar database is rebuilt. If an update/delete by UID fails, the error is logged and the `calendar_uid` is cleared so the event gets recreated on next sync.

## Soft-delete handling

When an exam/homework is soft-deleted (removed from API), delete the corresponding calendar event by UID and clear the `calendar_uid` column. The query for this: `WHERE deleted_at IS NOT NULL AND calendar_uid IS NOT NULL`.

## Module: `src/vulcan_notify/calendar.py`

Public interface:

```python
async def sync_to_calendar(db: Database) -> CalendarSyncResult:
    """Push all active exams/homework to macOS Calendar.

    Reads calendar_map and calendar_reminder_hours from settings.
    Joins students table to resolve student_key -> calendar name.

    - Creates events for items without calendar_uid
    - Updates events for items with calendar_uid
    - Deletes events for soft-deleted items that still have calendar_uid
    Returns summary of created/updated/deleted counts.
    """

@dataclass
class CalendarSyncResult:
    created: int
    updated: int
    deleted: int
    errors: int
```

Internal helpers:
- `_create_event(calendar_name, title, date, description, reminder_hours) -> str` - returns UID
- `_update_event(calendar_name, uid, title, date, description) -> None`
- `_delete_event(calendar_name, uid) -> None`
- `_run_applescript(script: str) -> str` - async subprocess wrapper using `asyncio.create_subprocess_exec`

Each AppleScript call uses async subprocess. Errors are logged and counted but don't abort the sync. If an update/delete fails (stale UID), the `calendar_uid` is cleared.

## Database changes

`src/vulcan_notify/db.py`:
- Add `calendar_uid TEXT` column to `exams` and `homework` tables (schema + migration)
- `set_calendar_uid(table, item_id, uid)` - store UID after event creation
- `clear_calendar_uid(table, item_id)` - clear after event deletion or stale UID
- `get_items_for_calendar(student_key)` - return all active exams/homework (where `deleted_at IS NULL`)
- `get_deleted_items_with_calendar_uid(student_key)` - return items where `deleted_at IS NOT NULL AND calendar_uid IS NOT NULL`

## Config changes

`src/vulcan_notify/config.py`:
```python
calendar_map: dict[str, str] = {}  # student name -> calendar name, empty = disabled
calendar_reminder_hours: int = 24  # alarm trigger (hours before)
```

## CLI integration

`src/vulcan_notify/__main__.py`:
- `sync` command: after data sync, if `calendar_map` is configured, call `sync_to_calendar(db)` and print summary line
- `calendar` command: delete all events with stored UIDs, clear all `calendar_uid` values, then recreate events for all active exams/homework. This is a clean re-sync.

## Verification

- Run `uv run vulcan-notify sync` and check macOS Calendar for new events
- Run `uv run vulcan-notify calendar` and verify all active items appear
- Verify duplicate prevention: run sync twice, count events stay the same
- Verify soft-delete: remove an exam from API, sync, check event is gone from calendar
- Run `uv run pytest` to verify no regressions
