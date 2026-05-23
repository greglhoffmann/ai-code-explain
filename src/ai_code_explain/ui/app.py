"""Textual TUI application — three-pane layout.

Layout:
  ┌──────────────┬───────────────────────────────────┐
  │ Snippet      │  Code Editor / Viewer             │
  │ History      │  (syntax-highlighted)             │
  │ Sidebar      ├───────────────────────────────────┤
  │              │  Analysis Tabs                    │
  │              │  [Explanation][Complexity]        │
  │              │  [Semgrep][Optimizations][Diff]   │
  └──────────────┴───────────────────────────────────┘

Keyboard navigation:
  Tab / Shift-Tab  — move focus between panes
  Enter            — load selected snippet / select hotspot
  Ctrl+n           — new snippet (open editor for input)
  Ctrl+r           — re-run analysis on current snippet
  Ctrl+q           — quit
  1-5              — switch analysis tab
"""

from __future__ import annotations

from pathlib import Path
from collections import defaultdict
from itertools import groupby
from typing import Optional

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import (
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Markdown,
    Static,
    TabPane,
    TabbedContent,
    TextArea,
)
from textual.widgets._text_area import LanguageDoesNotExist
from textual.widgets._footer import FooterKey, FooterLabel, KeyGroup

from ..database import (
    Snippet,
    deserialize_json_field,
    load_all_snippets,
    load_snippet_by_id,
)
from ..diff_generator import (
    generate_side_by_side_with_highlights,
    generate_rich_diff_markup,
)
from ..models import AnalysisResult, ASTSpan, BlockComplexity, ComplexityEstimate, Improvement, StaticMetadata


# ---------------------------------------------------------------------------
# Custom widgets
# ---------------------------------------------------------------------------


class CodeViewer(TextArea):
    """Read-only code viewer with selectable text and hotspot highlighting."""

    DEFAULT_CSS = """
    CodeViewer {
        height: 1fr;
        border: solid $primary;
    }
    CodeViewer .text-area--selection {
        background: $warning 35%;
        color: $text;
    }
    """

    _current_code: str = ""
    _language: str = "python"

    def on_mount(self) -> None:
        """Apply viewer defaults even before first code render."""
        self.read_only = True
        self.show_line_numbers = True
        self.soft_wrap = False
        self.theme = "monokai"

    def display_code(
        self,
        code: str,
        language: str = "python",
    ) -> None:
        """Load code into the read-only editor.

        Args:
            code: Source text to display.
            language: Pygments language identifier.
        """
        self._current_code = code
        self._language = language
        self.read_only = True
        self.show_line_numbers = True
        self.soft_wrap = False
        self.theme = "monokai"
        try:
            self.language = "python" if language == "python" else "javascript"
        except LanguageDoesNotExist:
            self.language = None
        self.text = code

    def highlight_span(self, start_line: int, end_line: int) -> None:
        """Select a line range to produce a visible background highlight."""
        if not self._current_code:
            return

        line_count = self.document.line_count
        if line_count <= 0:
            return

        start_row = max(0, min(start_line - 1, line_count - 1))
        end_row = max(0, min(end_line - 1, line_count - 1))
        end_col = len(self.document.get_line(end_row))

        # Use TextArea selection for an actual highlighted background range.
        self.move_cursor((start_row, 0), select=False, center=True)
        self.move_cursor((end_row, end_col), select=True, center=True)
        self.focus()


class OutputViewer(TextArea):
    """Read-only, selectable output text area for analysis panes."""

    DEFAULT_CSS = """
    OutputViewer {
        height: 1fr;
        border: solid $panel;
    }
    OutputViewer .text-area--selection {
        background: $warning 35%;
        color: $text;
    }
    """

    def on_mount(self) -> None:
        self.read_only = True
        self.show_line_numbers = False
        self.soft_wrap = True
        self.theme = "monokai"

    def display_text(self, text: str, soft_wrap: bool = True) -> None:
        self.read_only = True
        self.show_line_numbers = False
        self.soft_wrap = soft_wrap
        self.theme = "monokai"
        self.text = text


class DiffOutputViewer(TextArea):
    """Read-only diff viewer with per-line red/green background highlights.

    Extends TextArea so the diff is keyboard-selectable and shows line numbers
    in a separate gutter.  Diff row colours are injected into the TextArea's
    internal highlight map after the regular syntax-highlight pass.
    """

    DEFAULT_CSS = """
    DiffOutputViewer {
        height: 1fr;
        border: none;
    }
    DiffOutputViewer .text-area--selection {
        background: $warning 35%;
        color: $text;
    }
    """

    def on_mount(self) -> None:
        self.read_only = True
        self.show_line_numbers = True
        self.soft_wrap = False
        self._diff_row_styles: dict[int, str] = {}
        # Extend the monokai theme with diff-line background styles.
        from textual.widgets._text_area import TextAreaTheme  # local import — only needed here
        from rich.style import Style
        base = TextAreaTheme.get_builtin_theme("monokai")
        if base:
            extended = dict(base.syntax_styles)
            extended["diff.removed"] = Style(bgcolor="#4a1f1f")
            extended["diff.added"] = Style(bgcolor="#1f4a2d")
            self.register_theme(TextAreaTheme(
                name="monokai",
                base_style=base.base_style,
                gutter_style=base.gutter_style,
                cursor_style=base.cursor_style,
                cursor_line_style=base.cursor_line_style,
                cursor_line_gutter_style=base.cursor_line_gutter_style,
                bracket_matching_style=base.bracket_matching_style,
                selection_style=base.selection_style,
                syntax_styles=extended,
            ))
        self.theme = "monokai"

    def _build_highlight_map(self) -> None:
        """Inject diff row colours after the regular syntax highlight pass."""
        super()._build_highlight_map()
        for row, style_name in getattr(self, "_diff_row_styles", {}).items():
            self._highlights[row].append((0, None, style_name))

    def display_diff(
        self,
        code: str,
        removed_rows: list[int],
        added_rows: list[int],
    ) -> None:
        """Load plain-text diff content and mark changed lines with colours.

        Args:
            code: Plain source text (no Rich markup — the TextArea renders it).
            removed_rows: 0-indexed rows to highlight as removed (red).
            added_rows: 0-indexed rows to highlight as added (green).
        """
        self._diff_row_styles = {r: "diff.removed" for r in removed_rows}
        self._diff_row_styles.update({r: "diff.added" for r in added_rows})
        self.read_only = True
        self.show_line_numbers = True
        self.soft_wrap = False
        self.text = code  # triggers _build_highlight_map via _set_document


class InlineSnippetEditor(TextArea):
    """Snippet input editor with explicit app-level shortcut passthrough."""

    BINDINGS = [
        Binding("ctrl+s", "submit_snippet", "Analyze"),
        Binding("escape", "cancel_snippet", "Cancel"),
    ]

    def action_submit_snippet(self) -> None:
        app = self.app
        if isinstance(app, CodeExplainApp):
            app._submit_inline_editor()

    def action_cancel_snippet(self) -> None:
        app = self.app
        if isinstance(app, CodeExplainApp):
            app._close_editor()

    def action_toggle_model(self) -> None:
        app = self.app
        if isinstance(app, CodeExplainApp):
            app.action_toggle_model()

    def action_toggle_analysis_mode(self) -> None:
        app = self.app
        if isinstance(app, CodeExplainApp):
            app.action_toggle_analysis_mode()

    def action_toggle_block_complexity(self) -> None:
        app = self.app
        if isinstance(app, CodeExplainApp):
            app.action_toggle_block_complexity()

    def on_key(self, event) -> None:
        """Intercept toggle shortcuts before TextArea consumes them."""
        if event.key == "ctrl+t":
            self.action_toggle_model()
            event.stop()
            return
        if event.key == "ctrl+y":
            self.action_toggle_analysis_mode()
            event.stop()
            return
        if event.key == "ctrl+b":
            self.action_toggle_block_complexity()
            event.stop()
            return


class ModeAwareFooter(Footer):
    """Footer that presents stable, editor-specific key order in snippet mode."""

    def compose(self) -> ComposeResult:
        if not self._bindings_ready:
            return

        app = self.app
        if isinstance(app, CodeExplainApp) and app._show_input:
            active_bindings = self.screen.active_bindings
            bindings = [
                (binding, enabled, tooltip)
                for (_, binding, enabled, tooltip) in active_bindings.values()
                if binding.show
            ]
            desired_order = ["ctrl+q", "ctrl+s", "escape", "ctrl+t", "ctrl+y", "ctrl+b"]
            desired_set = set(desired_order)
            order_rank = {key: idx for idx, key in enumerate(desired_order)}
            filtered = [entry for entry in bindings if entry[0].key in desired_set]
            filtered.sort(key=lambda entry: order_rank.get(entry[0].key, 999))

            for binding, enabled, tooltip in filtered:
                yield FooterKey(
                    binding.key,
                    self.app.get_key_display(binding),
                    binding.description,
                    binding.action,
                    disabled=not enabled,
                    tooltip=tooltip,
                ).data_bind(compact=Footer.compact)
            return

        active_bindings = self.screen.active_bindings
        bindings = [
            (binding, enabled, tooltip)
            for (_, binding, enabled, tooltip) in active_bindings.values()
            if binding.show
        ]
        action_to_bindings: defaultdict[str, list[tuple[Binding, bool, str]]]
        action_to_bindings = defaultdict(list)
        for binding, enabled, tooltip in bindings:
            action_to_bindings[binding.action].append((binding, enabled, tooltip))

        self.styles.grid_size_columns = len(action_to_bindings)

        for group, multi_bindings_iterable in groupby(
            action_to_bindings.values(),
            lambda multi_bindings_: multi_bindings_[0][0].group,
        ):
            multi_bindings = list(multi_bindings_iterable)
            if group is not None and len(multi_bindings) > 1:
                with KeyGroup(classes="-compact" if group.compact else ""):
                    for multi_bindings in multi_bindings:
                        binding, enabled, tooltip = multi_bindings[0]
                        yield FooterKey(
                            binding.key,
                            self.app.get_key_display(binding),
                            "",
                            binding.action,
                            disabled=not enabled,
                            tooltip=tooltip or binding.description,
                            classes="-grouped",
                        ).data_bind(compact=Footer.compact)
                yield FooterLabel(group.description)
            else:
                for multi_bindings in multi_bindings:
                    binding, enabled, tooltip = multi_bindings[0]
                    yield FooterKey(
                        binding.key,
                        self.app.get_key_display(binding),
                        binding.description,
                        binding.action,
                        disabled=not enabled,
                        tooltip=tooltip,
                    ).data_bind(compact=Footer.compact)

        if self.show_command_palette and self.app.ENABLE_COMMAND_PALETTE:
            try:
                _node, binding, enabled, tooltip = active_bindings[
                    self.app.COMMAND_PALETTE_BINDING
                ]
            except KeyError:
                pass
            else:
                yield FooterKey(
                    binding.key,
                    self.app.get_key_display(binding),
                    binding.description,
                    binding.action,
                    classes="-command-palette",
                    disabled=not enabled,
                    tooltip=binding.tooltip or binding.description,
                )


class HotspotList(ListView):
    """Navigable list of AST hotspots for the current analysis."""

    DEFAULT_CSS = """
    HotspotList {
        height: auto;
        max-height: 12;
        border: solid $accent;
        margin-top: 1;
    }
    """

    def populate(self, hotspots: list[ASTSpan]) -> None:
        """Rebuild the list with new hotspot entries."""
        self.clear()
        if not hotspots:
            # Intentionally omit an ID so repeated repopulation never collides.
            self.append(ListItem(Label("No hotspots detected.")))
            return
        for span in hotspots:
            label = f"[{span.node_type}] {span.label or span.name} (L{span.start_line}–{span.end_line})"
            self.append(HotspotListItem(span.node_id, label))


class HotspotListItem(ListItem):
    """List item carrying hotspot node_id without relying on global widget IDs."""

    def __init__(self, node_id: str, label_text: str) -> None:
        super().__init__(Label(label_text))
        self.node_id = node_id


class SnippetListItem(ListItem):
    """A history-sidebar ListItem that carries its snippet_id as data.

    Uses a Python attribute rather than a widget ID to avoid duplicate-ID
    errors when the history list is cleared and repopulated.
    """

    def __init__(self, snippet_id: int, label_text: str) -> None:
        super().__init__(Label(label_text))
        self.snippet_id = snippet_id


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------


class CodeExplainApp(App):
    """Three-pane code analysis TUI."""

    TITLE = "AI Code Explain"
    SUB_TITLE = "Static + LLM Code Analysis"

    CSS = """
    Screen {
        layout: horizontal;
    }
    #sidebar {
        width: 28;
        border: solid $accent;
        height: 1fr;
    }
    #sidebar-title {
        background: $accent;
        color: $text;
        text-align: center;
        padding: 0 1;
    }
    #history-list {
        height: 1fr;
        overflow-y: scroll;
    }
    #main-area {
        width: 1fr;
        height: 1fr;
        layout: vertical;
    }
    #code-panel {
        height: 55%;
        border: solid $primary;
    }
    #analysis-panel {
        height: 45%;
        border: solid $secondary;
    }
    #input-area {
        height: 100%;
    }
    #code-input {
        height: 1fr;
    }
    #status-row {
        height: 1;
        background: $boost;
        layout: horizontal;
    }
    #status-bar {
        width: 1fr;
        padding: 0 1;
    }
    #status-model {
        width: auto;
        min-width: 26;
        text-align: right;
        padding: 0 1;
    }
    .tab-scroll {
        height: 1fr;
        padding: 1;
    }
    #diff-split {
        height: 1fr;
    }
    .diff-code {
        width: 1fr;
        border: none;
    }
    .diff-column {
        width: 1fr;
        border: solid $panel;
        height: 1fr;
    }
    .diff-title {
        background: $panel;
        padding: 0 1;
    }
    .diff-scroll {
        height: 1fr;
        padding: 0 1;
    }
    #diff-mode-tabs {
        height: 1fr;
    }
    #hotspot-panel {
        height: auto;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+n", "new_snippet", "New Snippet"),
        Binding("ctrl+r", "rerun_analysis", "Re-run"),
        Binding("ctrl+e", "export_optimized", "Export Optimized"),
        Binding("ctrl+t", "toggle_model", "Toggle Model"),
        Binding("ctrl+y", "toggle_analysis_mode", "Toggle Analysis Mode"),
        Binding("ctrl+b", "toggle_block_complexity", "Toggle Block Complexity"),
        Binding("ctrl+d", "cycle_diff_mode", "Cycle Diff View"),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("1", "switch_tab('explanation')", "Explanation", priority=True),
        Binding("2", "switch_tab('complexity')", "Complexity", priority=True),
        Binding("3", "switch_tab('semgrep')", "Semgrep", priority=True),
        Binding("4", "switch_tab('optimizations')", "Optimizations", priority=True),
        Binding("5", "switch_tab('diff')", "Diff", priority=True),
        Binding("tab", "focus_next", "Next pane", show=False),
    ]

    # Reactive state
    current_snippet_id: reactive[Optional[int]] = reactive(None)
    current_result: reactive[Optional[AnalysisResult]] = reactive(None)
    is_analyzing: reactive[bool] = reactive(False)
    model_mode: reactive[str] = reactive("fast")
    analysis_mode: reactive[str] = reactive("concise")
    block_complexity_enabled: reactive[bool] = reactive(False)
    diff_mode: reactive[str] = reactive("side")

    def __init__(self, pipeline_callback=None):
        """
        Args:
            pipeline_callback: Callable(code, language_hint) -> AnalysisResult
                               Injected by main.py to decouple UI from pipeline.
        """
        super().__init__()
        self._pipeline_callback = pipeline_callback
        self._current_hotspots: list[ASTSpan] = []
        self._pending_code: str = ""
        self._current_optimized_code: str = ""
        self._show_input: bool = False
        self._status_message: str = "Ready"
        self._analysis_status_message: str = ""
        self._last_diff_original: str = ""
        self._last_diff_optimized: str = ""
        self._last_diff_original_scroll: float = 0.0
        self._last_diff_optimized_scroll: float = 0.0

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()

        with Horizontal():
            # --- Left sidebar: snippet history ---
            with Vertical(id="sidebar"):
                yield Label("Snippet History", id="sidebar-title")
                yield ListView(id="history-list")

            # --- Main area: code viewer + analysis tabs ---
            with Vertical(id="main-area"):
                with Horizontal(id="status-row"):
                    yield Label("", id="status-bar")
                    yield Label("", id="status-model")

                with Container(id="code-panel"):
                    yield CodeViewer(id="code-viewer")

                with Container(id="analysis-panel"):
                    with TabbedContent(id="analysis-tabs"):
                        with TabPane("Explanation", id="explanation"):
                            with VerticalScroll(classes="tab-scroll", id="explanation-scroll"):
                                yield Markdown("*No analysis yet.*", id="explanation-text", open_links=False)
                            yield HotspotList(id="explanation-hotspots")

                        with TabPane("Complexity", id="complexity"):
                            with VerticalScroll(classes="tab-scroll", id="complexity-scroll"):
                                yield Markdown("*No analysis yet.*", id="complexity-text", open_links=False)
                            yield HotspotList(id="complexity-hotspots")

                        with TabPane("Semgrep", id="semgrep"):
                            with VerticalScroll(classes="tab-scroll", id="semgrep-scroll"):
                                yield Markdown("*No findings.*", id="semgrep-text", open_links=False)
                            yield HotspotList(id="semgrep-hotspots")

                        with TabPane("Optimizations", id="optimizations"):
                            with VerticalScroll(classes="tab-scroll", id="optimizations-scroll"):
                                yield Markdown("*No optimization suggestions yet.*", id="optimizations-text", open_links=False)
                            yield HotspotList(id="optimizations-hotspots")

                        

                        with TabPane("Diff", id="diff"):
                            with TabbedContent(id="diff-mode-tabs", initial="diff-side"):
                                with TabPane("Side-by-side", id="diff-side"):
                                    with Horizontal(id="diff-split"):
                                        with Vertical(classes="diff-column"):
                                            yield Label("Original", classes="diff-title")
                                            yield DiffOutputViewer("", id="diff-original-text", classes="diff-code")
                                        with Vertical(classes="diff-column"):
                                            yield Label("Optimized", classes="diff-title")
                                            yield DiffOutputViewer("", id="diff-optimized-text", classes="diff-code")

                                with TabPane("Unified", id="diff-unified"):
                                    with VerticalScroll(classes="tab-scroll"):
                                        yield Static("No diff available.", id="diff-unified-text")

        yield ModeAwareFooter()

    # ------------------------------------------------------------------
    # History sidebar population
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        """Load snippet history on startup."""
        self._refresh_history()
        self._update_status_bar()
        self._sync_diff_mode_tab()
        # Use a lightweight poll to keep split diff panes synchronized across
        # TextArea-based code views in this Textual version.
        self.set_interval(0.08, self._poll_sync_diff_scroll)

    def _refresh_history(self) -> None:
        """Reload the history ListView from the database."""
        history_list = self.query_one("#history-list", ListView)
        history_list.clear()
        snippets = load_all_snippets()
        for snippet in snippets:
            short = snippet.original_code[:40].replace("\n", " ")
            label = f"#{snippet.id} [{snippet.language}] {short}…"
            history_list.append(SnippetListItem(snippet.id, label))

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    @on(ListView.Selected, "#history-list")
    def on_history_selected(self, event: ListView.Selected) -> None:
        """Load a snippet from history when selected."""
        if isinstance(event.item, SnippetListItem):
            self._load_snippet(event.item.snippet_id)

    @on(ListView.Selected, "#explanation-hotspots")
    @on(ListView.Selected, "#complexity-hotspots")
    @on(ListView.Selected, "#semgrep-hotspots")
    @on(ListView.Selected, "#optimizations-hotspots")
    def on_hotspot_selected(self, event: ListView.Selected) -> None:
        """Scroll/highlight the code view to the selected hotspot."""
        if isinstance(event.item, HotspotListItem):
            node_id = event.item.node_id
        else:
            item_id = event.item.id or ""
            if not item_id.startswith("hs_"):
                return
            node_id = item_id.removeprefix("hs_")
        self._focus_hotspot(node_id)

        if event.list_view.id == "explanation-hotspots":
            self.query_one("#explanation-scroll", VerticalScroll).scroll_end(animate=False)
        elif event.list_view.id == "complexity-hotspots":
            self.query_one("#complexity-scroll", VerticalScroll).scroll_end(animate=False)
        elif event.list_view.id == "semgrep-hotspots":
            self.query_one("#semgrep-scroll", VerticalScroll).scroll_end(animate=False)
        elif event.list_view.id == "optimizations-hotspots":
            self.query_one("#optimizations-scroll", VerticalScroll).scroll_end(animate=False)

    @on(TabbedContent.TabActivated, "#diff-mode-tabs")
    def on_diff_mode_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Keep internal diff mode state in sync with nested diff tabs."""
        self.diff_mode = "unified" if event.tab.id == "diff-unified" else "side"

    @on(Markdown.LinkClicked, "#explanation-text")
    @on(Markdown.LinkClicked, "#complexity-text")
    @on(Markdown.LinkClicked, "#semgrep-text")
    @on(Markdown.LinkClicked, "#optimizations-text")
    def on_hotspot_link_clicked(self, event: Markdown.LinkClicked) -> None:
        """Handle in-text hotspot links in markdown output panes."""
        href = event.href or ""
        node_id = ""
        if href.startswith("hotspot://"):
            node_id = href.removeprefix("hotspot://")
        elif href.startswith("#hotspot-"):
            node_id = href.removeprefix("#hotspot-")
        if not node_id:
            return
        # Stop both default navigation and bubbling so external link handlers
        # can't open browser tabs for internal hotspot anchors.
        event.prevent_default()
        event.stop()
        self._focus_hotspot(node_id)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_new_snippet(self) -> None:
        """Open the inline code input dialog."""
        if self._show_input:
            self._set_status("Already in new snippet mode.", force=True)
            return
        self._show_inline_editor()

    def action_rerun_analysis(self) -> None:
        """Re-run analysis on the currently displayed snippet."""
        if self._show_input:
            self._set_status("Re-run is unavailable while editing a new snippet.", force=True)
            return
        if self.current_snippet_id is not None:
            snippet = load_snippet_by_id(self.current_snippet_id)
            if snippet:
                self._trigger_analysis(snippet.original_code, snippet.language)

    def action_toggle_model(self) -> None:
        """Toggle between fast (Laguna M.1) and reasoning (Nemotron 3 Super) models."""
        self.model_mode = "reasoning" if self.model_mode == "fast" else "fast"
        label = "Laguna M.1 (fast)" if self.model_mode == "fast" else "Nemotron 3 Super (reasoning)"
        self._set_status(f"Model switched to: {label}", force=True)

    def action_toggle_analysis_mode(self) -> None:
        """Toggle concise vs detailed analysis output mode."""
        self.analysis_mode = "detailed" if self.analysis_mode == "concise" else "concise"
        self._set_status(f"Analysis mode switched to: {self.analysis_mode}", force=True)

    def action_toggle_block_complexity(self) -> None:
        """Toggle optional per-block LLM complexity analysis."""
        self.block_complexity_enabled = not self.block_complexity_enabled
        state = "ON" if self.block_complexity_enabled else "OFF"
        self._set_status(f"Block-level complexity: {state}", force=True)

    def action_cycle_diff_mode(self) -> None:
        """Cycle through side/unified diff modes."""
        tabs = self.query_one("#diff-mode-tabs", TabbedContent)
        active = tabs.active or "diff-side"
        if active == "diff-unified":
            tabs.active = "diff-side"
            self.diff_mode = "side"
        else:
            tabs.active = "diff-unified"
            self.diff_mode = "unified"

    def action_export_optimized(self) -> None:
        """Write current optimized code to a local file and show the path."""
        code = self._current_optimized_code
        if not code.strip():
            self._set_status("No optimized code available to export.")
            return

        language = self.current_result.language if self.current_result else "python"
        extension = ".py" if language == "python" else ".js"
        snippet_part = str(self.current_snippet_id) if self.current_snippet_id is not None else "latest"
        export_dir = Path.cwd() / ".code_explain_exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        out_path = export_dir / f"snippet_{snippet_part}_optimized{extension}"
        out_path.write_text(code, encoding="utf-8")
        self._set_status(f"Optimized code exported: {out_path}")

    def action_switch_tab(self, tab_id: str) -> None:
        """Switch the analysis tab by id.

        Blurs the currently focused widget first so Textual's focus management
        cannot re-activate the previous tab to keep a hidden widget in view.
        """
        self.set_focus(None)
        tabs = self.query_one("#analysis-tabs", TabbedContent)
        tabs.active = tab_id

    # ------------------------------------------------------------------
    # Inline editor (simple TextArea overlay in code panel)
    # ------------------------------------------------------------------

    def _show_inline_editor(self) -> None:
        """Replace code panel content with a TextArea for snippet entry."""
        code_panel = self.query_one("#code-panel", Container)
        code_panel.remove_children()
        try:
            ta = InlineSnippetEditor(id="code-input", language="python", soft_wrap=False)
        except LanguageDoesNotExist:
            ta = InlineSnippetEditor(id="code-input", soft_wrap=False)
        code_panel.mount(ta)
        code_panel.mount(
            Label(
                "[bold]^s[/bold] analyze  |  [bold]Esc[/bold] cancel  |  "
                "[bold]^t[/bold] model  [bold]^y[/bold] mode  [bold]^b[/bold] block",
                id="editor-hint",
            )
        )
        self._show_input = True
        self.query_one("#code-input", TextArea).focus()

    def _submit_inline_editor(self) -> None:
        """Submit currently entered snippet from inline editor."""
        editor = self.query_one("#code-input", TextArea)
        code = editor.text
        if code.strip():
            self._close_editor()
            self._trigger_analysis(code)
        else:
            self._set_status("Snippet is empty. Enter code before analyzing.", force=True)

    def on_key(self, event) -> None:
        """Handle global key events for the inline editor."""
        if self._show_input:
            if event.key == "ctrl+s":
                self._submit_inline_editor()
                event.stop()
            elif event.key == "escape":
                self._close_editor()
                event.stop()
            elif event.key == "ctrl+t":
                self.action_toggle_model()
                event.stop()
            elif event.key == "ctrl+y":
                self.action_toggle_analysis_mode()
                event.stop()
            elif event.key == "ctrl+b":
                self.action_toggle_block_complexity()
                event.stop()

    def _close_editor(self) -> None:
        """Restore the code viewer after closing the inline editor."""
        self._show_input = False
        code_panel = self.query_one("#code-panel", Container)
        code_panel.remove_children()
        code_panel.mount(CodeViewer(id="code-viewer"))
        # Redisplay the last code if any
        if self._pending_code:
            self.query_one("#code-viewer", CodeViewer).display_code(
                self._pending_code, "python"
            )

    # ------------------------------------------------------------------
    # Analysis trigger
    # ------------------------------------------------------------------

    def _trigger_analysis(self, code: str, language_hint: str = "") -> None:
        """Run the analysis pipeline in a background worker."""
        if not self._pipeline_callback:
            self._set_status("No pipeline configured.", force=True)
            return

        self._pending_code = code
        model_label = "Laguna M.1" if self.model_mode == "fast" else "Nemotron 3 Super"
        self.is_analyzing = True
        block_label = " + block complexity" if self.block_complexity_enabled else ""
        self._set_analysis_status(
            f"Analyzing… ({model_label}, {self.analysis_mode}, {block_label})"
        )

        viewer = self.query_one("#code-viewer", CodeViewer)
        viewer.display_code(code, language_hint or "python")

        # Use Textual's worker system to run the pipeline off the main thread
        self._run_pipeline_worker(
            code,
            language_hint,
            self.model_mode,
            self.analysis_mode,
            self.block_complexity_enabled,
        )

    @work(thread=True, exclusive=True)
    def _run_pipeline_worker(
        self,
        code: str,
        language_hint: str,
        model_mode: str,
        analysis_mode: str,
        include_block_complexity: bool,
    ) -> None:
        """Background worker: runs pipeline and posts result to main thread."""
        def _progress(msg: str) -> None:
            self.call_from_thread(self._set_analysis_status, msg)

        try:
            result: AnalysisResult = self._pipeline_callback(
                code,
                language_hint,
                model_mode=model_mode,
                analysis_mode=analysis_mode,
                progress_callback=_progress,
                include_block_complexity=include_block_complexity,
            )
            self.call_from_thread(self._on_analysis_complete, result)
        except Exception as exc:  # pylint: disable=broad-except
            self.call_from_thread(self._on_analysis_error, str(exc))

    def _on_analysis_complete(self, result: AnalysisResult) -> None:
        """Update the UI with a completed analysis result."""
        self.is_analyzing = False
        self.current_result = result
        self._current_hotspots = result.static_metadata.hotspots
        self._current_optimized_code = result.optimized_code or ""

        # Update code viewer with syntax highlighting
        viewer = self.query_one("#code-viewer", CodeViewer)
        viewer.display_code(result.original_code, result.language)

        # Populate analysis tabs
        self._populate_explanation(result)
        self._populate_complexity(result)
        self._populate_semgrep(result)
        self._populate_optimizations(result)
        self._populate_diff(result)

        # Refresh history sidebar with new entry
        self._refresh_history()
        model_label = "Laguna M.1" if self.model_mode == "fast" else "Nemotron 3 Super"
        confidence = result.detection_confidence
        conf_suffix = f" (confidence: {confidence})" if confidence and confidence != "unknown" else ""
        self._set_status(
            f"Analysis complete — {result.language}{conf_suffix} | model: {model_label} | mode: {self.analysis_mode}",
            force=True,
        )

    def _on_analysis_error(self, error_message: str) -> None:
        """Display an error if the pipeline fails."""
        self.is_analyzing = False
        self._set_status(f"Error: {error_message}", force=True)
        self._refresh_history()
        self.query_one("#explanation-text", Markdown).update(
            f"**Analysis failed:**\n\n{error_message}"
        )

    # ------------------------------------------------------------------
    # Tab population
    # ------------------------------------------------------------------

    def _populate_explanation(self, result: AnalysisResult) -> None:
        """Fill the Explanation tab with text and hotspots."""
        text = result.explanation or "*No explanation returned.*"
        self._populate_linked_markdown(
            markdown_id="#explanation-text",
            hotspot_list_id="#explanation-hotspots",
            text=text,
            hotspots=result.static_metadata.hotspots,
        )

    def _populate_complexity(self, result: AnalysisResult) -> None:
        """Fill the Complexity tab."""
        confidence_label = result.detection_confidence
        conf_display = f" (detection confidence: {confidence_label})" if confidence_label and confidence_label != "unknown" else ""
        header = (
            f"## Complexity Analysis\n\n"
            f"**Language:** `{result.language}`{conf_display}\n\n"
        )

        if result.complexity is None:
            self.query_one("#complexity-text", Markdown).update(
                header + "*No complexity data.*"
            )
            return

        c = result.complexity
        reasoning_md = (c.llm_reasoning or "N/A").strip() or "N/A"
        text = (
            header
            + f"### Static Estimate (deterministic)\n"
            f"- **Time:** `{c.static_time}` (confidence: {c.static_confidence})\n"
            f"- **Space:** `{c.static_space}`\n\n"
            f"### LLM-Refined Estimate\n"
            f"- **Time:** `{c.llm_time or 'N/A'}` (confidence: {c.llm_confidence or 'N/A'})\n"
            f"- **Space:** `{c.llm_space or 'N/A'}`\n\n"
            f"### Reasoning\n\n{reasoning_md}\n"
        )

        if result.block_complexities:
            text += "\n\n### Per-Block Complexity\n"
            for block in result.block_complexities:
                text += (
                    f"- [{block.block_id}](#hotspot-{block.block_id}) "
                    f"({block.node_type}, L{block.start_line}-L{block.end_line})\n"
                    f"  - Static: `{block.static_time}` / `{block.static_space}` "
                    f"(confidence: {block.static_confidence})\n"
                    f"  - LLM: `{block.llm_time or 'N/A'}` / `{block.llm_space or 'N/A'}` "
                    f"(confidence: {block.llm_confidence or 'N/A'})\n"
                )

        # Show complexity-related hotspots (nested loops, sorts, recursion)
        complexity_spans = [
            h for h in result.static_metadata.hotspots
            if h.node_type in ("nested_loop", "sort", "recursion")
        ]
        if not complexity_spans:
            complexity_spans = result.static_metadata.hotspots

        linked_text = self._linkify_hotspots(text, result.static_metadata.hotspots)
        self.query_one("#complexity-text", Markdown).update(linked_text)
        self.query_one("#complexity-hotspots", HotspotList).populate(complexity_spans)

    def _populate_optimizations(self, result: AnalysisResult) -> None:
        """Fill the Optimizations tab with every model-returned improvement."""
        lines: list[str] = ["## Optimization Suggestions", ""]

        if result.optimized_code.strip():
            language = "python" if result.language == "python" else "javascript"
            lines.append("### Canonical Optimized Source")
            lines.append(f"```{language}")
            lines.append(result.optimized_code.rstrip())
            lines.append("```")
            lines.append("")

        if not result.improvements:
            lines.append("*No optimization suggestions returned.*")
            self._populate_linked_markdown(
                markdown_id="#optimizations-text",
                hotspot_list_id="#optimizations-hotspots",
                text="\n".join(lines),
                hotspots=result.static_metadata.hotspots,
            )
            return

        for index, improvement in enumerate(result.improvements, start=1):
            description_md = self._linkify_hotspots(
                improvement.description or "N/A",
                result.static_metadata.hotspots,
            )
            tradeoffs_md = self._linkify_hotspots(
                improvement.tradeoffs or "N/A",
                result.static_metadata.hotspots,
            )
            lines.append(
                f"### {index}. {improvement.category.title()} ({improvement.impact}, risk: {improvement.behavior_change_risk})"
            )
            lines.append("#### Description")
            lines.append("")
            lines.append(description_md)
            lines.append("")
            lines.append("#### Tradeoffs")
            lines.append("")
            lines.append(tradeoffs_md)
            lines.append("")
            if improvement.optimized_code.strip():
                if improvement.optimized_code.strip() == result.optimized_code.strip():
                    lines.append("- **Optimized Code:** Refer to Canonical Optimized Source")
                elif improvement.optimized_code.strip().lower() == "refer to canonical optimized source":
                    lines.append("- **Optimized Code:** Refer to Canonical Optimized Source")
                else:
                    lines.append("- **Optimized Code:**")
                    lines.append("```")
                    lines.append(improvement.optimized_code.rstrip())
                    lines.append("```")
            lines.append("")

        self._populate_linked_markdown(
            markdown_id="#optimizations-text",
            hotspot_list_id="#optimizations-hotspots",
            text="\n".join(lines).strip(),
            hotspots=result.static_metadata.hotspots,
        )

    def _populate_semgrep(self, result: AnalysisResult) -> None:
        """Fill the Semgrep tab."""
        lines: list[str] = []

        if result.sandbox_warnings:
            lines.append("## Sandbox Warnings\n")
            lines.append("The following imports resolved to files **outside the sandbox** and were skipped:\n")
            for name in result.sandbox_warnings:
                lines.append(f"- `{name}`")
            lines.append("")

        if result.optimization_warnings:
            lines.append("## Optimization Warnings\n")
            for warning in result.optimization_warnings:
                lines.append(f"- {warning}")
            lines.append("")

        if result.semgrep_findings:
            lines.append("## Semgrep Findings\n")
            hotspots = result.static_metadata.hotspots
            for finding in result.semgrep_findings:
                severity = finding.get("severity", "info").upper()
                rule = finding.get("rule", "")
                message = finding.get("message", "")
                fline = finding.get("line", 0)
                # Find hotspot that covers this finding's line
                linked_hs = next(
                    (h for h in hotspots if h.start_line <= fline <= h.end_line),
                    None,
                )
                if linked_hs:
                    loc = f"[line {fline}](#hotspot-{linked_hs.node_id})"
                else:
                    loc = f"line {fline}"
                lines.append(f"- **[{severity}]** `{rule}` ({loc}): {message}")

        if not lines:
            self.query_one("#semgrep-text", Markdown).update("*No Semgrep findings.*")
        else:
            self.query_one("#semgrep-text", Markdown).update("\n".join(lines))

        # Populate hotspot list for keyboard navigation
        self.query_one("#semgrep-hotspots", HotspotList).populate(
            result.static_metadata.hotspots
        )

    def _populate_diff(self, result: AnalysisResult) -> None:
        """Fill the Diff tab with side-by-side original/optimized views."""
        original = result.original_code or ""
        optimized = result.optimized_code or result.original_code or ""
        self._last_diff_original = original
        self._last_diff_optimized = optimized
        self._render_diff_views()

    def _render_diff_views(self) -> None:
        """Render all diff modes from current original/optimized buffers."""
        left_code, left_changed, right_code, right_changed = generate_side_by_side_with_highlights(
            self._last_diff_original,
            self._last_diff_optimized,
        )
        self.query_one("#diff-original-text", DiffOutputViewer).display_diff(
            left_code, removed_rows=left_changed, added_rows=[]
        )
        self.query_one("#diff-optimized-text", DiffOutputViewer).display_diff(
            right_code, removed_rows=[], added_rows=right_changed
        )

        unified_markup = generate_rich_diff_markup(self._last_diff_original, self._last_diff_optimized)
        self.query_one("#diff-unified-text", Static).update(unified_markup)

        self._sync_diff_mode_tab()

    def _sync_diff_mode_tab(self) -> None:
        """Select the nested diff tab that matches diff_mode."""
        tabs = self.query_one("#diff-mode-tabs", TabbedContent)
        tabs.active = "diff-unified" if self.diff_mode == "unified" else "diff-side"

    def _poll_sync_diff_scroll(self) -> None:
        """Keep side-by-side diff panes vertically synchronized."""
        if self.diff_mode != "side":
            return

        try:
            left = self.query_one("#diff-original-text", DiffOutputViewer)
            right = self.query_one("#diff-optimized-text", DiffOutputViewer)
        except Exception:  # pylint: disable=broad-except
            return

        left_y = float(left.scroll_y)
        right_y = float(right.scroll_y)
        left_changed = abs(left_y - self._last_diff_original_scroll) > 0.2
        right_changed = abs(right_y - self._last_diff_optimized_scroll) > 0.2

        if left_changed and not right_changed:
            right.scroll_to(y=left_y, animate=False)
            right_y = float(right.scroll_y)
        elif right_changed and not left_changed:
            left.scroll_to(y=right_y, animate=False)
            left_y = float(left.scroll_y)

        self._last_diff_original_scroll = left_y
        self._last_diff_optimized_scroll = right_y

    # ------------------------------------------------------------------
    # Snippet load from history
    # ------------------------------------------------------------------

    def _snippet_to_result(self, snippet: Snippet) -> AnalysisResult:
        """Reconstruct a partial AnalysisResult from a persisted Snippet record."""
        static_data = deserialize_json_field(snippet.static_complexity_json) or {}
        llm_data = deserialize_json_field(snippet.llm_complexity_json) or {}
        static_est = static_data.get("static_estimate", {})
        llm_est = llm_data.get("llm_adjusted_estimate", {})
        block_ests = llm_data.get("block_estimates", [])

        complexity = ComplexityEstimate(
            static_time=static_est.get("time", "N/A"),
            static_space=static_est.get("space", "N/A"),
            static_confidence=static_est.get("confidence", "high"),
            llm_time=llm_est.get("time"),
            llm_space=llm_est.get("space"),
            llm_confidence=llm_est.get("confidence"),
            llm_reasoning=llm_est.get("reasoning"),
        )
        block_complexities = [BlockComplexity(**b) for b in block_ests]

        # Reconstruct hotspots from stored JSON
        raw_hotspots = static_data.get("hotspots", [])
        hotspots = [ASTSpan(**h) for h in raw_hotspots]

        metadata = StaticMetadata(
            language=snippet.language,
            baseline_time_complexity=static_est.get("time", "O(n)"),
            baseline_space_complexity=static_est.get("space", "O(n)"),
            hotspots=hotspots,
        )

        referenced_hotspots = llm_data.get("referenced_hotspots", [])
        raw_improvements = llm_data.get("improvements", [])
        improvements = [Improvement(**improvement) for improvement in raw_improvements]
        optimization_warnings = llm_data.get("optimization_warnings", [])

        return AnalysisResult(
            language=snippet.language,
            original_code=snippet.original_code,
            static_metadata=metadata,
            semgrep_findings=deserialize_json_field(snippet.semgrep_findings_json) or [],
            explanation=snippet.explanation or "",
            referenced_hotspots=referenced_hotspots,
            complexity=complexity,
            block_complexities=block_complexities,
            improvements=improvements,
            optimized_code=snippet.optimized_code or "",
            optimization_warnings=optimization_warnings,
        )

    def _load_snippet(self, snippet_id: int) -> None:
        """Display a previously saved snippet from the database."""
        snippet = load_snippet_by_id(snippet_id)
        if snippet is None:
            return

        # History loading can be triggered while inline snippet input is open.
        # Ensure the standard code viewer is mounted before rendering snippet code.
        if self._show_input:
            self._close_editor()

        self.current_snippet_id = snippet_id
        self._pending_code = snippet.original_code
        self._current_optimized_code = snippet.optimized_code or ""

        viewer = self.query_one("#code-viewer", CodeViewer)
        viewer.display_code(snippet.original_code, snippet.language or "python")

        # Restore analysis data from DB
        self._restore_from_snippet(snippet)
        model_label = "Laguna M.1" if self.model_mode == "fast" else "Nemotron 3 Super"
        confidence = self.current_result.detection_confidence if self.current_result else ""
        conf_suffix = f" (confidence: {confidence})" if confidence and confidence != "unknown" else ""
        language = self.current_result.language if self.current_result else (snippet.language or "unknown")
        self._set_status(
            f"Loaded snippet #{snippet_id}: {language}{conf_suffix} | model: {model_label} | mode: {self.analysis_mode}"
        )

    def _restore_from_snippet(self, snippet: Snippet) -> None:
        """Populate analysis tabs from a persisted Snippet record."""
        result = self._snippet_to_result(snippet)
        self.current_result = result
        self._current_hotspots = result.static_metadata.hotspots
        self._populate_explanation(result)
        self._populate_complexity(result)
        self._populate_semgrep(result)
        self._populate_optimizations(result)
        self._populate_diff(result)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _linkify_hotspots(text: str, hotspots: list[ASTSpan]) -> str:
        """Replace bare hotspot node_id occurrences in text with markdown links."""
        import re
        for h in hotspots:
            # Match the node_id when not already inside a markdown link
            pattern = re.compile(
                r"(?<!\[)(?<!#hotspot-)\b(" + re.escape(h.node_id) + r")\b(?!\])"
            )
            replacement = f"[{h.node_id}](#hotspot-{h.node_id})"
            text = pattern.sub(replacement, text)
        return text

    def _populate_linked_markdown(
        self,
        markdown_id: str,
        hotspot_list_id: str,
        text: str,
        hotspots: list[ASTSpan],
    ) -> None:
        """Populate markdown text and a matching hotspot list using shared logic."""
        linked = self._linkify_hotspots(text, hotspots)
        self.query_one(markdown_id, Markdown).update(linked)
        self.query_one(hotspot_list_id, HotspotList).populate(hotspots)

    def _focus_hotspot(self, node_id: str) -> None:
        """Highlight hotspot in code viewer by node id."""
        for span in self._current_hotspots:
            if span.node_id == node_id:
                viewer = self.query_one("#code-viewer", CodeViewer)
                viewer.highlight_span(span.start_line, span.end_line)
                break

    def _model_label(self) -> str:
        """Return concise model indicator label."""
        model = "Laguna M.1 (fast)" if self.model_mode == "fast" else "Nemotron 3 Super (reasoning)"
        block = "ON" if self.block_complexity_enabled else "OFF"
        return f"Model: {model} | Mode: {self.analysis_mode} | Block Complexity: {block}"

    def watch_model_mode(self, _: str) -> None:
        """Refresh model indicator when mode changes."""
        self._update_status_bar()

    def watch_is_analyzing(self, _: bool) -> None:
        """Refresh status bar when running state changes."""
        self._update_status_bar()

    def watch_block_complexity_enabled(self, _: bool) -> None:
        """Refresh status bar when block complexity toggle changes."""
        self._update_status_bar()

    def watch_analysis_mode(self, _: str) -> None:
        """Refresh status bar when analysis mode changes."""
        self._update_status_bar()

    def _update_status_bar(self) -> None:
        """Update left status text and right model indicator."""
        left = self._status_message
        if self.is_analyzing and self._analysis_status_message:
            left = f"[running] {self._analysis_status_message}"
        self.query_one("#status-bar", Label).update(left)
        self.query_one("#status-model", Label).update(self._model_label())

    def _set_analysis_status(self, message: str) -> None:
        """Update running-progress status text without losing run visibility."""
        self._analysis_status_message = message
        self._update_status_bar()

    def _set_status(self, message: str, force: bool = False) -> None:
        """Update general status; while analyzing this is ignored unless forced."""
        if self.is_analyzing and not force:
            return
        self._status_message = message
        if force and not self.is_analyzing:
            self._analysis_status_message = ""
        self._update_status_bar()
