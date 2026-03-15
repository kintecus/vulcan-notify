"""Terminal output formatting for sync results."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vulcan_notify.differ import Change
    from vulcan_notify.sync import SyncResult

# ANSI escape codes - disabled when not a TTY
_is_tty = sys.stdout.isatty()

BOLD = "\033[1m" if _is_tty else ""
RED = "\033[91m" if _is_tty else ""
GREEN = "\033[92m" if _is_tty else ""
YELLOW = "\033[93m" if _is_tty else ""
BLUE = "\033[94m" if _is_tty else ""
RESET = "\033[0m" if _is_tty else ""


def _prefix_for_change(change: Change) -> str:
    if change.change_type == "new" and change.item_type == "attendance":
        return f"{RED}!{RESET}"
    if change.change_type == "new":
        return f"{GREEN}+{RESET}"
    if change.change_type == "updated":
        return f"{YELLOW}~{RESET}"
    return " "


def format_change(change: Change) -> str:
    """Format a single change line."""
    prefix = _prefix_for_change(change)
    return f"    {prefix} {change.title}"


def format_sync_results(results: list[SyncResult]) -> str:
    """Format all sync results for terminal display."""
    lines: list[str] = []

    for result in results:
        header = f"{BOLD}{result.student.name} ({result.student.class_name}){RESET}"
        lines.append(header)

        if result.is_first_sync:
            lines.append("  Initial sync complete (baseline stored)")
            lines.append("")
            continue

        if not result.has_changes:
            lines.append("  No changes since last sync.")
        else:
            if result.new_grades:
                lines.append(f"  {BOLD}Grades:{RESET}")
                for change in result.new_grades:
                    lines.append(format_change(change))

            if result.new_attendance:
                lines.append(f"  {BOLD}Attendance:{RESET}")
                for change in result.new_attendance:
                    lines.append(format_change(change))

            if result.new_exams:
                lines.append(f"  {BOLD}Exams:{RESET}")
                for change in result.new_exams:
                    lines.append(format_change(change))

            if result.new_homework:
                lines.append(f"  {BOLD}Homework:{RESET}")
                for change in result.new_homework:
                    lines.append(format_change(change))

        if result.unread_messages:
            lines.append(f"  {BLUE}Unread messages: {result.unread_messages}{RESET}")

        lines.append("")

    return "\n".join(lines)


def filter_messages_by_whitelist(
    senders: list[str],
    whitelist: list[str],
) -> list[str]:
    """Filter message senders by whitelist.

    If whitelist is empty, returns all senders.
    Matching is case-insensitive substring match.
    """
    if not whitelist:
        return senders
    return [s for s in senders if any(w.lower() in s.lower() for w in whitelist)]
