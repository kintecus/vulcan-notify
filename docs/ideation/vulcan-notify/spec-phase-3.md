# Implementation spec: vulcan-notify - Phase 3

**Contract**: ./contract.md
**Depends on**: Phase 2 (sync + diff engine)
**Estimated effort**: S

## Phase overview

Polish the CLI output so `vulcan-notify sync` produces clear, human-readable terminal output. Implement message sender whitelist filtering. Clean up old iris-related code and update documentation.

## Technical approach

Add a `display.py` module that formats `SyncResult` into readable terminal output. Use simple ANSI escape codes for color (no heavy dependency like rich) - bold for headers, color for change types. Group output by student, then by data type.

Message filtering applies the configured sender whitelist at display time (all messages are still stored in the database for completeness).

## Feedback strategy

**Inner-loop command**: `uv run vulcan-notify sync`

**Playground**: The CLI tool itself - run sync after each change to see output.

**Why this approach**: This phase is mostly formatting and display. The real tool with real data is the fastest way to validate output quality.

## File changes

### New files

| File path | Purpose |
|-----------|---------|
| `src/vulcan_notify/display.py` | Terminal output formatting for sync results |
| `tests/test_display.py` | Tests for output formatting |

### Modified files

| File path | Changes |
|-----------|---------|
| `src/vulcan_notify/__main__.py` | Use display module for sync output; remove placeholder prints |
| `src/vulcan_notify/config.py` | Ensure `message_sender_whitelist` parsing works with comma-separated env var |
| `.env.example` | Add `MESSAGE_SENDER_WHITELIST` example |
| `CLAUDE.md` | Update architecture description for new modules |
| `README.md` | Update setup/usage instructions for cookie-based auth and sync command |
| `pyproject.toml` | Remove iris from dependencies |

### Deleted files

| File path | Reason |
|-----------|--------|
| `src/vulcan_notify/notifier.py` | Not used in v1 CLI-only mode; will be re-added when notification delivery is implemented |
| `tests/test_notifier.py` | Tests for removed module |

## Implementation details

### Display module (`display.py`)

**Overview**: Formats sync results for terminal output. Uses ANSI codes for minimal color without dependencies.

```python
BOLD = "\033[1m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"

def format_sync_results(
    results: list[SyncResult],
    whitelist: list[str],
) -> str:
    """Format sync results for terminal display."""
    ...

def format_grade(change: Change) -> str:
    """Format a single grade change."""
    ...

def format_attendance(change: Change) -> str:
    """Format a single attendance change."""
    ...
```

**Output structure**:
```
Alice (1A)
  Grades:
    + 5p in Math - Sprawdzian 1 (teacher: Smith A.)
    ~ 4 -> 5 in Polish - Kartkowka 3 (teacher: Brown J.)
  Attendance:
    ! Absent: 2026-03-14, lesson 3 (Przyroda)
  Exams:
    Przyroda - 2026-03-16 (quiz)
  Homework:
    Plastyka - due 2026-03-16

Bob (4B)
  No changes since last sync.

Messages (whitelisted senders):
  From: Jones A. - "Zebranie z rodzicami" (2026-03-14)
```

**Key decisions**:
- `+` prefix for new items, `~` for updated, `!` for attention items (absence)
- Group by student with class name
- Messages shown separately (they're account-level, not per-student)
- "No changes" message when nothing new for a student
- Disable ANSI if stdout is not a TTY (piping to file)

**Implementation steps**:
1. Implement `format_sync_results()` with student grouping
2. Add grade formatting (new vs updated)
3. Add attendance formatting (highlight absences)
4. Add exam/homework formatting (upcoming items)
5. Add message filtering by whitelist and formatting
6. Add TTY detection for ANSI toggle

**Feedback loop**:
- **Playground**: Run `uv run vulcan-notify sync` after each change
- **Experiment**: Sync with changes (verify formatting). Sync with no changes (verify "no changes" message). Pipe to file and verify no ANSI codes. Set whitelist and verify message filtering.
- **Check command**: `uv run vulcan-notify sync`

### Message whitelist filtering

**Overview**: Filter messages at display time based on configured sender names.

```python
def filter_messages(
    messages: list[Message],
    whitelist: list[str],
) -> list[Message]:
    """Filter messages to only include whitelisted senders.

    If whitelist is empty, returns all messages.
    Matching is case-insensitive substring match.
    """
    if not whitelist:
        return messages
    return [
        m for m in messages
        if any(w.lower() in m.sender.lower() for w in whitelist)
    ]
```

**Key decisions**:
- Substring match (not exact) so "Jones" matches "Jones Anna [JA]"
- Case-insensitive
- Empty whitelist = show all (no filtering)

### Cleanup

**Implementation steps**:
1. Remove `iris` from `pyproject.toml` dependencies and run `uv sync`
2. Delete `notifier.py` and `test_notifier.py` (will be re-added in notification phase)
3. Update `CLAUDE.md` to reflect new architecture (client -> sync -> diff -> display)
4. Update `README.md` with new commands (`auth`, `test`, `sync`)
5. Update `.env.example` with `MESSAGE_SENDER_WHITELIST`

## Testing requirements

### Unit tests

| Test file | Coverage |
|-----------|----------|
| `tests/test_display.py` | Output formatting, whitelist filtering, ANSI toggling |

**Key test cases**:
- Formats new grade with `+` prefix and correct details
- Formats updated grade with `~` prefix showing old -> new
- Shows "No changes" for student with no changes
- Whitelist filters messages correctly (substring, case-insensitive)
- Empty whitelist returns all messages
- ANSI codes absent when not TTY

### Manual testing

- [ ] `uv run vulcan-notify sync` shows readable output with both kids' data
- [ ] Adding `MESSAGE_SENDER_WHITELIST` in `.env` filters messages
- [ ] `uv run vulcan-notify sync | cat` produces no ANSI escape sequences
- [ ] `uv run vulcan-notify sync` with no changes since last run says "no changes"

## Validation commands

```bash
uv run pytest tests/ -x -q
uv run mypy src/
uv run ruff check .
uv run vulcan-notify sync  # Manual visual check
```

---

_This spec is ready for implementation. Follow the patterns and validate at each step._
