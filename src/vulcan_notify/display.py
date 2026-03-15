"""Terminal output formatting for sync results."""

from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vulcan_notify.differ import Change
    from vulcan_notify.models import Message
    from vulcan_notify.sync import FullSyncResult

# ANSI escape codes - disabled when not a TTY
_is_tty = sys.stdout.isatty()

BOLD = "\033[1m" if _is_tty else ""
DIM = "\033[2m" if _is_tty else ""
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


def _strip_html(html: str) -> str:
    """Minimal HTML-to-text: strip tags, decode entities, collapse whitespace."""
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _format_sender_short(sender: str) -> str:
    """Shorten sender like 'Kruczek Patrycja - P - (ZSIJP)' to 'Kruczek P.'."""
    parts = sender.split(" - ")
    name_part = parts[0].strip() if parts else sender
    name_words = name_part.split()
    if len(name_words) >= 2:
        return f"{name_words[0]} {name_words[1][0]}."
    return name_part


def format_message(msg: Message, show_content: bool = True) -> list[str]:
    """Format a single message for display."""
    sender = _format_sender_short(msg.sender)
    lines = [f'    {GREEN}+{RESET} From: {sender} - "{msg.subject}"']
    if msg.mailbox:
        # Extract kid name from mailbox like "Senyuk Ostap - R - Senyuk Yarema - (ZSIJP)"
        mailbox_parts = msg.mailbox.split(" - ")
        if len(mailbox_parts) >= 3:
            kid_name = mailbox_parts[2].strip().split(" - ")[0]
            lines[0] += f" {DIM}[{kid_name}]{RESET}"
    if show_content and msg.content:
        text = _strip_html(msg.content)
        # Show first 200 chars of content
        preview = text[:200]
        if len(text) > 200:
            preview += "..."
        for line in preview.split("\n"):
            if line.strip():
                lines.append(f"      {DIM}{line.strip()}{RESET}")
    return lines


def format_full_sync(
    result: FullSyncResult,
    whitelist: list[str] | None = None,
) -> str:
    """Format complete sync results including messages."""
    lines: list[str] = []

    # Student results
    for sr in result.student_results:
        header = f"{BOLD}{sr.student.name} ({sr.student.class_name}){RESET}"
        lines.append(header)

        if sr.is_first_sync:
            lines.append("  Initial sync complete (baseline stored)")
            lines.append("")
            continue

        if not sr.has_changes:
            lines.append("  No changes since last sync.")
        else:
            if sr.new_grades:
                lines.append(f"  {BOLD}Grades:{RESET}")
                for change in sr.new_grades:
                    lines.append(format_change(change))

            if sr.new_attendance:
                lines.append(f"  {BOLD}Attendance:{RESET}")
                for change in sr.new_attendance:
                    lines.append(format_change(change))

            if sr.new_exams:
                lines.append(f"  {BOLD}Exams:{RESET}")
                for change in sr.new_exams:
                    lines.append(format_change(change))

            if sr.new_homework:
                lines.append(f"  {BOLD}Homework:{RESET}")
                for change in sr.new_homework:
                    lines.append(format_change(change))

        lines.append("")

    # Messages section
    if result.is_first_message_sync:
        lines.append(f"{BOLD}Messages{RESET}")
        lines.append("  Initial sync complete (baseline stored)")
        lines.append("")
    elif result.new_messages:
        filtered = filter_messages(result.new_messages, whitelist or [])
        if filtered:
            lines.append(f"{BOLD}Messages ({len(filtered)} new){RESET}")
            for msg in filtered:
                lines.extend(format_message(msg))
            lines.append("")
        skipped = len(result.new_messages) - len(filtered)
        if skipped > 0:
            lines.append(f"  {DIM}({skipped} messages filtered by whitelist){RESET}")
            lines.append("")

    return "\n".join(lines)


def filter_messages(
    messages: list[Message],
    whitelist: list[str],
) -> list[Message]:
    """Filter messages by sender whitelist.

    If whitelist is empty, returns all messages.
    Matching is case-insensitive substring match.
    """
    if not whitelist:
        return messages
    return [m for m in messages if any(w.lower() in m.sender.lower() for w in whitelist)]


def filter_messages_by_whitelist(
    senders: list[str],
    whitelist: list[str],
) -> list[str]:
    """Filter sender name strings by whitelist (kept for backwards compat)."""
    if not whitelist:
        return senders
    return [s for s in senders if any(w.lower() in s.lower() for w in whitelist)]
