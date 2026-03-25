"""Interactive TUI for browsing synced data."""

from __future__ import annotations

from typing import ClassVar

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Static,
    TabbedContent,
    TabPane,
)

from vulcan_notify.config import settings
from vulcan_notify.db import Database
from vulcan_notify.display import _format_sender_short, _strip_html

ATTENDANCE_CATEGORIES: dict[int, str] = {
    1: "Present",
    2: "Absent",
    3: "Late",
    4: "Excused",
}

EXAM_TYPES: dict[int, str] = {
    1: "Test",
    2: "Quiz",
}

TAB_NAMES: list[str] = ["messages", "grades", "attendance", "exams", "homework"]
TAB_LABELS: list[str] = ["Messages", "Grades", "Attendance", "Exams", "Homework"]

HELP_TEXT = """\
[b]Navigation[/b]
  1-5          Switch tabs (1=Messages .. 5=Homework)
  j/k, arrows  Move cursor down / up
  Enter        Open detail view
  Escape / q   Back (detail) or quit (main)

[b]Sorting[/b]
  o            Cycle sort column
  O            Reverse sort direction

[b]Filtering[/b]
  s            Cycle student filter

[b]Other[/b]
  ?            Toggle this help
  g / G        Jump to first / last row
"""


class HelpScreen(ModalScreen[None]):
    """Keyboard reference overlay."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close_help", "Close", show=False),
        Binding("question_mark", "close_help", "Close", show=False),
        Binding("q", "close_help", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Static(HELP_TEXT, id="help-panel")

    def action_close_help(self) -> None:
        self.dismiss()


class DetailScreen(Screen[None]):
    """Generic detail view with metadata fields and optional body text."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "go_back", "Back"),
        Binding("q", "go_back", "Back"),
    ]

    def __init__(
        self,
        title: str,
        fields: list[tuple[str, str]],
        body: str | None = None,
    ) -> None:
        super().__init__()
        self._title = title
        self._fields = fields
        self._body = body

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with VerticalScroll():
            meta_lines = [f"[b]{label}:[/b] {value}" for label, value in self._fields]
            yield Static("\n".join(meta_lines), classes="metadata")
            if self._body:
                yield Static(self._body, classes="content")
        yield Footer()

    def action_go_back(self) -> None:
        self.app.pop_screen()


class MainScreen(Screen[None]):
    """Main screen with tabbed navigation for all entity types."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit_app", "Quit"),
        Binding("question_mark", "toggle_help", "? Help"),
        # Tab switching via number keys
        Binding("1", "switch_tab('messages')", "1 Msgs", show=False),
        Binding("2", "switch_tab('grades')", "2 Grades", show=False),
        Binding("3", "switch_tab('attendance')", "3 Attend", show=False),
        Binding("4", "switch_tab('exams')", "4 Exams", show=False),
        Binding("5", "switch_tab('homework')", "5 HW", show=False),
        # Sorting
        Binding("o", "cycle_sort", "Sort"),
        Binding("O", "reverse_sort", "Reverse"),
        # Filtering
        Binding("s", "cycle_student", "Student"),
        # Navigation (vim + arrows, priority=True to bypass tab bar focus)
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("g", "scroll_top", "Top", show=False),
        Binding("G", "scroll_bottom", "Bottom", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._students: list[dict[str, object]] = []
        self._student_filter: str | None = None
        self._student_filter_index: int = 0
        self._data: dict[str, list[dict[str, object]]] = {}
        self._loaded_tabs: set[str] = set()
        self._sort_state: dict[str, tuple[int, bool]] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static("", id="status-bar")
        with TabbedContent(*TAB_LABELS):
            for name in TAB_NAMES:
                with TabPane(name.capitalize(), id=f"tab-{name}"):
                    yield DataTable(id=f"{name}-table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#messages-table", DataTable).add_columns(
            "Date", "Sender", "Subject"
        )
        self.query_one("#grades-table", DataTable).add_columns(
            "Date", "Student", "Subject", "Grade", "Category", "Weight"
        )
        self.query_one("#attendance-table", DataTable).add_columns(
            "Date", "Student", "Lesson", "Status", "Subject"
        )
        self.query_one("#exams-table", DataTable).add_columns(
            "Date", "Student", "Subject", "Type", "Description"
        )
        self.query_one("#homework-table", DataTable).add_columns(
            "Date", "Student", "Subject", "Content"
        )
        self._load_students_and_first_tab()

    # ── Data loading ──────────────────────────────────────────────────

    @work
    async def _load_students_and_first_tab(self) -> None:
        app: VulcanTuiApp = self.app  # type: ignore[assignment]
        self._students = await app.db.get_all_students()
        await self._load_tab("messages")

    @on(TabbedContent.TabActivated)
    def _on_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        tab_id = event.pane.id or ""
        tab_name = tab_id.removeprefix("tab-")
        if tab_name not in self._loaded_tabs:
            self._load_tab_work(tab_name)
        self._update_status_bar()

    @work
    async def _load_tab_work(self, tab_name: str) -> None:
        await self._load_tab(tab_name)

    async def _load_tab(self, tab_name: str) -> None:
        app: VulcanTuiApp = self.app  # type: ignore[assignment]
        self._loaded_tabs.add(tab_name)

        if tab_name == "messages":
            data = await app.db.get_all_messages()
            self._data["messages"] = data
            table = self.query_one("#messages-table", DataTable)
            table.clear()
            for msg in data:
                table.add_row(
                    str(msg.get("date", ""))[:10],
                    _format_sender_short(str(msg.get("sender", ""))),
                    str(msg.get("subject", "")),
                )
        else:
            await self._load_student_tab(tab_name)
        self._update_status_bar()

    async def _load_student_tab(self, tab_name: str) -> None:
        app: VulcanTuiApp = self.app  # type: ignore[assignment]
        table = self.query_one(f"#{tab_name}-table", DataTable)
        table.clear()
        all_rows: list[dict[str, object]] = []

        for student in self._students:
            student_key = str(student["key"])
            student_name = str(student["name"])

            if self._student_filter and student_key != self._student_filter:
                continue

            if tab_name == "grades":
                rows = await app.db.get_grades_for_student(student_key)
                for r in rows:
                    r["student_name"] = student_name
                    table.add_row(
                        str(r.get("date", ""))[:10],
                        student_name,
                        str(r.get("subject", "")),
                        str(r.get("value", "")),
                        str(r.get("column_name", "")),
                        str(r.get("weight", "")),
                    )
                all_rows.extend(rows)

            elif tab_name == "attendance":
                rows = await app.db.get_attendance_for_student(student_key)
                for r in rows:
                    r["student_name"] = student_name
                    cat = r.get("category")
                    cat_int = int(cat) if isinstance(cat, int) else 0
                    status = ATTENDANCE_CATEGORIES.get(cat_int, f"Category {cat_int}")
                    table.add_row(
                        str(r.get("date", ""))[:10],
                        student_name,
                        str(r.get("lesson_number", "")),
                        status,
                        str(r.get("subject", "")),
                    )
                all_rows.extend(rows)

            elif tab_name == "exams":
                rows = await app.db.get_exams_for_student(student_key)
                for r in rows:
                    r["student_name"] = student_name
                    exam_type_int = r.get("type")
                    exam_type = EXAM_TYPES.get(
                        int(exam_type_int) if isinstance(exam_type_int, int) else 0,
                        "Exam",
                    )
                    desc = str(r.get("description", "") or "")
                    table.add_row(
                        str(r.get("date", ""))[:10],
                        student_name,
                        str(r.get("subject", "")),
                        exam_type,
                        desc[:50],
                    )
                all_rows.extend(rows)

            elif tab_name == "homework":
                rows = await app.db.get_homework_for_student(student_key)
                for r in rows:
                    r["student_name"] = student_name
                    content = str(r.get("content", "") or "")
                    if content:
                        content = _strip_html(content)
                    table.add_row(
                        str(r.get("date", ""))[:10],
                        student_name,
                        str(r.get("subject", "")),
                        content[:50],
                    )
                all_rows.extend(rows)

        self._data[tab_name] = all_rows
        self._update_status_bar()

    # ── Sorting ───────────────────────────────────────────────────────

    _SORT_KEYS: ClassVar[dict[str, list[str]]] = {
        "messages": ["date", "sender", "subject"],
        "grades": ["date", "student_name", "subject", "value", "column_name", "weight"],
        "attendance": ["date", "student_name", "lesson_number", "category", "subject"],
        "exams": ["date", "student_name", "subject", "type", "description"],
        "homework": ["date", "student_name", "subject", "content"],
    }

    _COLUMN_NAMES: ClassVar[dict[str, list[str]]] = {
        "messages": ["Date", "Sender", "Subject"],
        "grades": ["Date", "Student", "Subject", "Grade", "Category", "Weight"],
        "attendance": ["Date", "Student", "Lesson", "Status", "Subject"],
        "exams": ["Date", "Student", "Subject", "Type", "Description"],
        "homework": ["Date", "Student", "Subject", "Content"],
    }

    @on(DataTable.HeaderSelected)
    def _on_header_selected(self, event: DataTable.HeaderSelected) -> None:
        tab_name = self._get_active_tab_name()
        col_idx = event.column_index
        prev = self._sort_state.get(tab_name)
        reverse = not prev[1] if prev and prev[0] == col_idx else False
        self._sort_state[tab_name] = (col_idx, reverse)
        self._apply_sort(tab_name)

    def action_cycle_sort(self) -> None:
        tab_name = self._get_active_tab_name()
        keys = self._SORT_KEYS.get(tab_name, [])
        if not keys:
            return
        prev = self._sort_state.get(tab_name)
        col_idx = ((prev[0] + 1) if prev else 1) % len(keys)
        self._sort_state[tab_name] = (col_idx, False)
        self._apply_sort(tab_name)

    def action_reverse_sort(self) -> None:
        tab_name = self._get_active_tab_name()
        prev = self._sort_state.get(tab_name)
        if prev:
            self._sort_state[tab_name] = (prev[0], not prev[1])
        else:
            self._sort_state[tab_name] = (0, True)
        self._apply_sort(tab_name)

    def _apply_sort(self, tab_name: str) -> None:
        col_idx, reverse = self._sort_state.get(tab_name, (0, False))
        keys = self._SORT_KEYS.get(tab_name, [])
        if col_idx < len(keys):
            sort_key = keys[col_idx]
            rows = self._data.get(tab_name, [])
            rows.sort(key=lambda r: str(r.get(sort_key, "")), reverse=reverse)
        self._render_table(tab_name)
        self._update_status_bar()

    # ── Table rendering ───────────────────────────────────────────────

    def _render_table(self, tab_name: str) -> None:
        """Re-render table rows from self._data without re-fetching."""
        table = self.query_one(f"#{tab_name}-table", DataTable)
        table.clear()
        for r in self._data.get(tab_name, []):
            if tab_name == "messages":
                table.add_row(
                    str(r.get("date", ""))[:10],
                    _format_sender_short(str(r.get("sender", ""))),
                    str(r.get("subject", "")),
                )
            elif tab_name == "grades":
                table.add_row(
                    str(r.get("date", ""))[:10],
                    str(r.get("student_name", "")),
                    str(r.get("subject", "")),
                    str(r.get("value", "")),
                    str(r.get("column_name", "")),
                    str(r.get("weight", "")),
                )
            elif tab_name == "attendance":
                cat = r.get("category")
                cat_int = int(cat) if isinstance(cat, int) else 0
                status = ATTENDANCE_CATEGORIES.get(cat_int, f"Category {cat_int}")
                table.add_row(
                    str(r.get("date", ""))[:10],
                    str(r.get("student_name", "")),
                    str(r.get("lesson_number", "")),
                    status,
                    str(r.get("subject", "")),
                )
            elif tab_name == "exams":
                exam_type_int = r.get("type")
                exam_type = EXAM_TYPES.get(
                    int(exam_type_int) if isinstance(exam_type_int, int) else 0, "Exam"
                )
                desc = str(r.get("description", "") or "")
                table.add_row(
                    str(r.get("date", ""))[:10],
                    str(r.get("student_name", "")),
                    str(r.get("subject", "")),
                    exam_type,
                    desc[:50],
                )
            elif tab_name == "homework":
                content = str(r.get("content", "") or "")
                if content:
                    content = _strip_html(content)
                table.add_row(
                    str(r.get("date", ""))[:10],
                    str(r.get("student_name", "")),
                    str(r.get("subject", "")),
                    content[:50],
                )

    # ── Detail views ──────────────────────────────────────────────────

    def _get_active_tab_name(self) -> str:
        tc = self.query_one(TabbedContent)
        pane_id = tc.active or ""
        return pane_id.removeprefix("tab-")

    @on(DataTable.RowSelected)
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        tab_name = self._get_active_tab_name()
        rows = self._data.get(tab_name, [])
        if event.cursor_row >= len(rows):
            return
        self._open_detail(tab_name, rows[event.cursor_row])

    def _open_detail(self, tab_name: str, row: dict[str, object]) -> None:
        if tab_name == "messages":
            sender = str(row.get("sender", ""))
            content = row.get("content")
            body = _strip_html(str(content)) if content and isinstance(content, str) else None
            fields = [
                ("From", sender),
                ("Subject", str(row.get("subject", ""))),
                ("Date", str(row.get("date", ""))[:10]),
            ]
            mailbox = str(row.get("mailbox", ""))
            if mailbox:
                fields.append(("Mailbox", mailbox))
            self.app.push_screen(DetailScreen("Message", fields, body))

        elif tab_name == "grades":
            fields = [
                ("Student", str(row.get("student_name", ""))),
                ("Subject", str(row.get("subject", ""))),
                ("Grade", str(row.get("value", ""))),
                ("Category", str(row.get("column_name", ""))),
                ("Weight", str(row.get("weight", ""))),
                ("Teacher", str(row.get("teacher", ""))),
                ("Date", str(row.get("date", ""))[:10]),
            ]
            self.app.push_screen(DetailScreen("Grade", fields))

        elif tab_name == "attendance":
            cat = row.get("category")
            cat_int = int(cat) if isinstance(cat, int) else 0
            status = ATTENDANCE_CATEGORIES.get(cat_int, f"Category {cat_int}")
            time_from = str(row.get("time_from", "") or "")
            time_to = str(row.get("time_to", "") or "")
            time_str = f"{time_from} - {time_to}" if time_from else ""
            fields = [
                ("Student", str(row.get("student_name", ""))),
                ("Date", str(row.get("date", ""))[:10]),
                ("Lesson", str(row.get("lesson_number", ""))),
                ("Status", status),
                ("Subject", str(row.get("subject", ""))),
                ("Teacher", str(row.get("teacher", ""))),
            ]
            if time_str:
                fields.append(("Time", time_str))
            self.app.push_screen(DetailScreen("Attendance", fields))

        elif tab_name == "exams":
            exam_type_int = row.get("type")
            exam_type = EXAM_TYPES.get(
                int(exam_type_int) if isinstance(exam_type_int, int) else 0, "Exam"
            )
            desc = row.get("description")
            body = _strip_html(str(desc)) if desc and isinstance(desc, str) else None
            fields = [
                ("Student", str(row.get("student_name", ""))),
                ("Date", str(row.get("date", ""))[:10]),
                ("Subject", str(row.get("subject", ""))),
                ("Type", exam_type),
                ("Teacher", str(row.get("teacher", "") or "")),
            ]
            self.app.push_screen(DetailScreen("Exam", fields, body))

        elif tab_name == "homework":
            content = row.get("content")
            body = _strip_html(str(content)) if content and isinstance(content, str) else None
            fields = [
                ("Student", str(row.get("student_name", ""))),
                ("Date", str(row.get("date", ""))[:10]),
                ("Subject", str(row.get("subject", ""))),
                ("Teacher", str(row.get("teacher", "") or "")),
            ]
            self.app.push_screen(DetailScreen("Homework", fields, body))

    # ── Actions ───────────────────────────────────────────────────────

    def action_quit_app(self) -> None:
        self.app.exit()

    def action_toggle_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def action_switch_tab(self, tab_name: str) -> None:
        tc = self.query_one(TabbedContent)
        tc.active = f"tab-{tab_name}"

    def action_cursor_down(self) -> None:
        table = self._get_active_table()
        table.action_cursor_down()

    def action_cursor_up(self) -> None:
        table = self._get_active_table()
        table.action_cursor_up()

    def action_scroll_top(self) -> None:
        table = self._get_active_table()
        table.action_scroll_home()

    def action_scroll_bottom(self) -> None:
        table = self._get_active_table()
        table.action_scroll_end()

    def _get_active_table(self) -> DataTable:  # type: ignore[type-arg]
        tab_name = self._get_active_tab_name()
        return self.query_one(f"#{tab_name}-table", DataTable)

    def action_cycle_student(self) -> None:
        if not self._students:
            return
        self._student_filter_index = (self._student_filter_index + 1) % (
            len(self._students) + 1
        )
        if self._student_filter_index == 0:
            self._student_filter = None
        else:
            student = self._students[self._student_filter_index - 1]
            self._student_filter = str(student["key"])

        self._update_status_bar()
        self._reload_active_tab()

    @work
    async def _reload_active_tab(self) -> None:
        tab_name = self._get_active_tab_name()
        if tab_name == "messages":
            return
        await self._load_student_tab(tab_name)

    # ── Status bar ────────────────────────────────────────────────────

    def _update_status_bar(self) -> None:
        tab_name = self._get_active_tab_name()
        row_count = len(self._data.get(tab_name, []))

        # Student filter
        if self._student_filter is None:
            student_part = "All"
        else:
            student_part = next(
                (str(s["name"]) for s in self._students
                 if str(s["key"]) == self._student_filter),
                "?",
            )

        # Sort indicator
        col_idx, reverse = self._sort_state.get(tab_name, (0, False))
        col_names = self._COLUMN_NAMES.get(tab_name, [])
        col_name = col_names[col_idx] if col_idx < len(col_names) else "?"
        arrow = "v" if reverse else "^"

        # Tab indicators: [1] [2] [3] [4] [5]
        tab_idx = TAB_NAMES.index(tab_name) if tab_name in TAB_NAMES else 0
        tabs = "  ".join(
            f"[b][{i + 1}]{TAB_LABELS[i][:4]}[/b]" if i == tab_idx
            else f"[{i + 1}]{TAB_LABELS[i][:4]}"
            for i in range(len(TAB_NAMES))
        )

        self.query_one("#status-bar", Static).update(
            f"{tabs}    {student_part} | {col_name}{arrow} | {row_count} rows"
        )


class VulcanTuiApp(App[None]):
    """Vulcan-notify terminal UI."""

    TITLE = "vulcan-notify"

    CSS = """
    #status-bar {
        dock: top;
        height: 1;
        padding: 0 2;
        background: $surface;
        color: $text-muted;
    }

    MainScreen DataTable {
        height: 1fr;
    }

    DetailScreen .metadata {
        padding: 1 2;
        background: $surface;
    }

    DetailScreen .content {
        padding: 1 2;
    }

    #help-panel {
        width: 50;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        margin: 2 4;
        background: $surface;
        border: round $accent;
        content-align: center middle;
    }

    HelpScreen {
        align: center middle;
        background: $background 60%;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.db = Database(settings.db_path)

    async def on_mount(self) -> None:
        await self.db.connect()
        self.push_screen(MainScreen())

    async def on_unmount(self) -> None:
        await self.db.close()


async def run_tui() -> None:
    """Entry point for the TUI."""
    app = VulcanTuiApp()
    await app.run_async()
