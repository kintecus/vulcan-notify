# Implementation spec: vulcan-notify - Phase 2

**Contract**: ./contract.md
**Depends on**: Phase 1 (API client + data model)
**Estimated effort**: M

## Phase overview

Implement the `sync` command: fetch all data for all students via `VulcanClient`, store it in the database, and detect what changed since the last sync. This is the core data pipeline - the heart of the tool.

## Technical approach

The sync command orchestrates: load session -> create client -> fetch students -> for each student, fetch grades + attendance + dashboard data -> upsert into database -> compare against previous state -> return list of changes.

The differ is redesigned from the iris-based version. Instead of hashing raw API objects, we compare against stored database rows. A "change" is anything that exists in the API response but not in the database (new), or exists in both but with different values (updated).

Sync state is tracked per student with a `last_synced_at` timestamp, so the CLI can report "changes since last sync."

## Feedback strategy

**Inner-loop command**: `uv run pytest tests/test_sync.py -x -q`

**Playground**: Test suite exercising the full sync pipeline with mocked client and real (temp) database.

**Why this approach**: The sync pipeline is a data transformation chain. Tests with realistic fixtures validate the full flow without hitting the network.

## File changes

### New files

| File path | Purpose |
|-----------|---------|
| `src/vulcan_notify/sync.py` | Sync orchestrator - fetches, stores, diffs |
| `tests/test_sync.py` | Integration tests for the sync pipeline |
| `tests/fixtures.py` | Shared test fixtures (fake students, grades, etc.) |

### Modified files

| File path | Changes |
|-----------|---------|
| `src/vulcan_notify/__main__.py` | Wire up `cmd_sync()` using the sync module |
| `src/vulcan_notify/db.py` | Add `get_grades_for_student()`, `get_attendance_for_student()` query methods needed by diff |
| `src/vulcan_notify/differ.py` | Rewrite to compare API data against database rows instead of hashing |

### Deleted files

| File path | Reason |
|-----------|--------|
| `src/vulcan_notify/poller.py` | Replaced by sync.py; polling loop will be added in a future daemon phase |

## Implementation details

### Sync orchestrator (`sync.py`)

**Overview**: Coordinates the full sync cycle for all students.

```python
@dataclass
class SyncResult:
    """Result of a single sync cycle."""
    student: Student
    new_grades: list[Grade]
    new_attendance: list[AttendanceEntry]
    new_exams: list[Exam]
    new_homework: list[Homework]
    unread_messages: int

    @property
    def has_changes(self) -> bool:
        return bool(self.new_grades or self.new_attendance
                     or self.new_exams or self.new_homework)

async def sync_student(
    client: VulcanClient,
    db: Database,
    student: Student,
) -> SyncResult:
    """Sync a single student's data and return detected changes."""
    ...

async def sync_all(
    client: VulcanClient,
    db: Database,
) -> list[SyncResult]:
    """Sync all students and return changes for each."""
    ...
```

**Key decisions**:
- `sync_student` handles one student end-to-end: fetch, store, diff, return changes
- `sync_all` iterates students sequentially (not concurrent) to avoid rate limiting
- First sync (no `last_synced_at` in db) stores everything and returns empty changes (baseline)
- Grade sync fetches the current period only (most recent from `OkresyKlasyfikacyjne`)
- Attendance sync fetches the current week (Mon-Sun) to catch recent changes

**Implementation steps**:
1. Implement `sync_student()` for grades: fetch periods -> get current -> fetch grades -> upsert -> diff against stored
2. Add attendance sync: fetch current week -> upsert -> diff
3. Add dashboard sync: exams, homework from Tablica endpoints -> upsert -> diff
4. Add message count tracking (just the unread count for now)
5. Implement `sync_all()` that iterates students
6. Handle first-run baseline (store all, report nothing)
7. Update `last_synced_at` per student after successful sync

**Feedback loop**:
- **Playground**: `tests/test_sync.py` with mocked `VulcanClient` and real temp database
- **Experiment**: First sync (baseline, no changes reported). Second sync with same data (no changes). Third sync with one new grade added (detected as new). Sync with changed grade value (detected as update).
- **Check command**: `uv run pytest tests/test_sync.py -x -q`

### Differ rewrite (`differ.py`)

**Pattern to follow**: Current `differ.py` structure (keep `Change` dataclass)

**Overview**: Compare fetched API data against stored database rows. Replace hash-based comparison with field-level comparison.

```python
@dataclass
class Change:
    """A detected change in school data."""
    change_type: str  # "new" or "updated"
    item_type: str  # "grade", "attendance", "exam", "homework"
    student_name: str
    title: str
    body: str
    priority: int = 3
    tags: list[str] | None = None

async def diff_grades(
    student: Student,
    fetched: list[Grade],
    db: Database,
) -> list[Change]:
    """Compare fetched grades against stored grades."""
    ...

async def diff_attendance(
    student: Student,
    fetched: list[AttendanceEntry],
    db: Database,
) -> list[Change]:
    """Compare fetched attendance against stored records."""
    ...

async def diff_exams(
    student: Student,
    fetched: list[Exam],
    db: Database,
) -> list[Change]:
    ...

async def diff_homework(
    student: Student,
    fetched: list[Homework],
    db: Database,
) -> list[Change]:
    ...
```

**Key decisions**:
- `Change.student_name` is always included (multi-kid support)
- Grade diff: new `column_id` = new grade; same `column_id` but different `value` = updated grade
- Attendance diff: new `(date, lesson_number)` combo = new record
- Exam/homework diff: new `id` = new item (these don't typically update)
- `Change` dataclass retains ntfy-compatible fields (priority, tags) for future notification support

**Implementation steps**:
1. Update `Change` dataclass with `change_type` and `student_name`
2. Implement `diff_grades()`: query stored grades for student, compare by column_id
3. Implement `diff_attendance()`: query stored attendance, compare by (date, lesson_number)
4. Implement `diff_exams()` and `diff_homework()`: compare by id
5. Update test fixtures to use new model types

**Feedback loop**:
- **Playground**: Update `tests/test_differ.py` to use new model types instead of `FakeGrade`
- **Experiment**: New grade detected, same grade not reported twice, changed grade value detected, new attendance detected. Multi-student: changes scoped to correct student.
- **Check command**: `uv run pytest tests/test_differ.py -x -q`

### CLI sync command (`__main__.py`)

**Overview**: Wire the sync pipeline into the CLI.

```python
async def cmd_sync() -> None:
    """Fetch latest data and show changes since last sync."""
    session = load_session(settings.session_file)
    client = VulcanClient(session)
    db = Database(settings.db_path)
    await db.connect()

    try:
        results = await sync_all(client, db)
        # Output handled in Phase 3; for now, just print summary
        for result in results:
            if result.has_changes:
                print(f"{result.student.name}: {len(result.new_grades)} new grades, ...")
            else:
                print(f"{result.student.name}: no changes")
    except SessionExpiredError:
        print("Session expired. Run 'vulcan-notify auth' to re-authenticate.")
        sys.exit(1)
    finally:
        await db.close()
```

**Implementation steps**:
1. Add `sync` to the match statement in `main()`
2. Wire up client, db, and sync_all
3. Basic print output (rich formatting is Phase 3)
4. Handle `SessionExpiredError` gracefully

## Testing requirements

### Unit tests

| Test file | Coverage |
|-----------|----------|
| `tests/test_differ.py` | All diff functions with new model types |
| `tests/test_sync.py` | Full sync pipeline: first run, no changes, new data, updated data |

**Key test cases**:
- First sync stores baseline, reports no changes
- Second sync with identical data reports no changes
- Sync with one new grade detects it as new
- Sync with changed grade value detects it as updated
- Multi-student sync returns separate results per student
- Session expired during sync raises clear error

### Manual testing

- [ ] `uv run vulcan-notify sync` fetches and stores data for both kids
- [ ] Running sync twice shows "no changes"
- [ ] After a real grade/attendance change on Vulcan, sync detects it

## Error handling

| Error scenario | Handling strategy |
|----------------|-------------------|
| Session expired mid-sync | Raise `SessionExpiredError`, print message, exit 1 |
| Single endpoint fails | Log warning, continue syncing other data types |
| Database locked | Let aiosqlite handle retry; if persistent, fail with clear message |
| No students in Context | Print "No students found" and exit |

## Validation commands

```bash
uv run pytest tests/ -x -q
uv run mypy src/
uv run ruff check .
```

## Open items

- [ ] Decide attendance date range for sync (current week? last 7 days? configurable?)
- [ ] Whether to fetch full grades or just Tablica summary for change detection (full is more data but more reliable)

---

_This spec is ready for implementation. Follow the patterns and validate at each step._
