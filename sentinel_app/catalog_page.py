"""The rule library: a read-only view of the detection repository, separate from live Azure rules."""
import json
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog
from . import ui_kit as kit
from .advice import lookup
from .desktop_theme import px
from .repository_catalog import GitHubReader, load_local, state
from .preview_dialog import PreviewDialog
from .assignment_dialog import AssignmentDialog
from .workflow_history_dialog import WorkflowHistoryDialog
from .rule_assignments import load_request as load_assignment_request
from .rules_page import STATES, STATE_ORDER, CopyValues, Split, RawConfig, detail_pane, flow, fit_columns, link_search

HEADINGS = {"Clients": ("Client", "State", "Workspace", "Target"), "Rules": ("Rule", "State", "Severity", "File")}
# Narrow starting widths that stretch, as in the workspace list (see rules_page.COLUMNS).
COLUMNS = (("name", "Rule", 90, True), ("state", "State", 100, False), ("info", "Severity", 130, False),
           ("path", "File", 100, True))
OPTIONAL = dict(path=620)  # The file or target joins the list once it is this wide (96-DPI px).
SOURCE_SUBTITLE = "Read the rule library from GitHub, or from a folder on this PC."
DETAIL_SUBTITLE = "Select a client or rule to see its assignments and configuration."
MONO_KEYS = {"File", "Rule ID", "Target"}


def friendly_time(value):
    """A library's load time in local words: 'today at 14:02' or '12 Oct, 14:02'."""
    text = str(value or "")
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" UTC", "+00:00")).astimezone()
    except (ValueError, TypeError):
        return text
    now = datetime.now().astimezone()
    return ("today at " if moment.date() == now.date() else moment.strftime("%d %b, ")) + moment.strftime("%H:%M")


def plural(count, word):
    return f"{count} {word}{'s' if count != 1 else ''}"


def overrides_text(overrides):
    """A client's per-rule overrides in a few words: 'enabled: false', or 'None'."""
    return ", ".join(f"{key}: {json.dumps(value, default=str)}" for key, value in (overrides or {}).items()) or "None"


def row_values(row):
    return (row["name"], row["state"], row.get("workspace", row.get("severity")), row.get("target", row["path"]))


class CatalogPage:
    def __init__(self, app, parent):
        self.app, self.snapshot, self.visible = app, None, []
        self.mode = tk.StringVar(value="Local folder")
        self.source = tk.StringVar()
        self.search = tk.StringVar()
        view = app.ui_state.get("library_view")
        self.view = tk.StringVar(value=view if isinstance(view, str) and view in HEADINGS else "Rules")
        self.filter = tk.StringVar(value="All states")
        self.sort = ("name", False)
        self.source_open = False  # The analyst asked to change the source.
        self.searchable = (None, {})  # (snapshot, {kind: search text per row}), built once per library.
        self.note = tk.StringVar()  # Guidance about the last command, shown above the command bar.
        self.shown = None  # The client or rule in the details pane.
        self.loading = False  # A read of the library is running (searching meanwhile keeps saying so).
        self.build_source(parent)
        self.split = Split(parent, app.ui_state.get("library_split", 0.6))
        self.split.pack(fill="both", expand=True)
        self.build_library()
        self.build_detail()
        self.render_soon = kit.debounce(self.tree, 150, self.render)
        link_search(self.search_entry, self.tree, self.render_soon)
        self.tree.bind("<<TreeviewSelect>>", self.selected)
        self.tree.bind("<Double-1>", self.open_row)
        self.tree.bind("<Return>", self.open_row)
        self.mode.trace_add("write", self.mode_changed)
        self.source.trace_add("write", self.invalidate)
        self.search.trace_add("write", self.render_soon)
        self.view.trace_add("write", lambda *args: (self.note.set(""), self.render()))
        self.filter.trace_add("write", lambda *args: self.render())
        self.note.trace_add("write", lambda *args: self.show_note())
        if "/" in self.github_repository(): self.mode.set("GitHub repository")
        else: self.mode_changed()
        self.render()

    # ------------------------------------------------------------- layout
    def build_source(self, parent):
        app = self.app
        card = self.source_card = kit.Card(parent, "Library source", SOURCE_SUBTITLE, icon="library")
        card.frame.pack(fill="x", pady=(0, kit.space("lg")))
        self.refresh_button = app.button(card.actions, "Refresh library", self.load)
        self.refresh_button.pack(side="right"); kit.tooltip(self.refresh_button, "Read the library again (F5)")
        # Once a library is loaded the card shrinks to one line: where it came from, and a way back.
        self.compact = ttk.Frame(card.body, style="Card.TFrame")
        self.status = tk.StringVar()
        kit.wrap_to_parent(ttk.Label(self.compact, textvariable=self.status, style="Hint.TLabel", justify="left", width=1),
                           minimum=px(120)).pack(side="left", fill="x", expand=True, padx=(0, px(12)))
        self.change_button = app.button(self.compact, "Change source…", lambda: self.show_source(True),
                                        style="Small.Ghost.TButton")
        self.change_button.pack(side="right")
        # The full form, on one row: where to read from, which repository or folder, and its buttons.
        self.form = ttk.Frame(card.body, style="Card.TFrame")
        row = ttk.Frame(self.form, style="Card.TFrame"); row.pack(fill="x")
        kit.Segmented(row, self.mode, (("GitHub repository", "GitHub"), ("Local folder", "Folder on this PC")),
                      enabled=lambda: not app.busy).pack(side="left", padx=(0, px(12)))
        self.source_entry = ttk.Entry(row, textvariable=self.source); self.source_entry.pack(side="left", fill="x", expand=True)
        app.controls.append((self.source_entry, "normal"))
        self.source_label = kit.placeholder(self.source_entry, self.source, "")  # Names what to type, per mode.
        self.choose_button = app.button(row, "Choose folder…", self.choose)
        self.cancel_button = app.button(row, "Cancel", lambda: self.show_source(False), style="Ghost.TButton")
        kit.tooltip(self.cancel_button, "Keep the library that is loaded")
        self.source_hint = tk.StringVar()
        self.hint_label = kit.wrap_to_parent(ttk.Label(self.form, textvariable=self.source_hint, style="Hint.TLabel",
                                                       justify="left", wraplength=px(600)))
        self.source_hint.trace_add("write", lambda *args: self.show_hint())
        self.error = tk.StringVar()
        self.error_banner = kit.Banner(self.form, self.error, "danger")

    def build_library(self):
        app = self.app
        library = kit.Card(self.split, "Rule library", icon="rules")
        self.split.add(library.frame, weight=3)
        self.summary_pill = kit.Pill(library.actions, "Not loaded")
        self.summary_pill.pack(side="right")
        self.summary_pill.bind("<Button-1>", lambda event: self.issues() if self.issue_count() else None)
        kit.tooltip(self.summary_pill, "")
        self.menu_button = app.button(library.actions, "Library ⋯", self.library_menu, style="Small.Ghost.TButton")
        self.menu_button.pack(side="right", padx=(0, px(8)))
        kit.tooltip(self.menu_button, "Library issues, saved assignments and preview history")
        self.menu = kit.popup_menu(app.root)
        body = library.body
        bar = ttk.Frame(body, style="Card.TFrame"); bar.pack(fill="x", pady=(0, px(12)))
        bar.columnconfigure(1, weight=1)
        self.view_bar = kit.Segmented(bar, self.view, (("Rules", "Rules"), ("Clients", "Clients")), compact=True)
        self.view_bar.grid(row=0, column=0, sticky="w")
        self.search_entry = ttk.Entry(bar, textvariable=self.search)
        self.search_entry.grid(row=0, column=1, sticky="ew", padx=(px(12), 0))
        app.inputs.append(self.search_entry)  # Searching stays possible while a task runs.
        kit.placeholder(self.search_entry, self.search, "Search the library  (Ctrl+F)", search=True)
        self.filter_bar = kit.Segmented(bar, self.filter, STATES, compact=True)
        self.place_filters = flow(bar, self.filter_bar, 2)
        self.message = kit.Banner(body, self.note, "info")
        # The commands for the selected rule sit right above the list.
        self.commands = ttk.Frame(body, style="Card.TFrame"); self.commands.pack(fill="x", pady=(0, px(10)))
        self.edit_button = ttk.Button(self.commands, text="Edit selected rule", style="Small.Primary.TButton",
                                      command=self.edit_selected, state="disabled")
        kit.tooltip(self.edit_button, "Open the rule in the Rule builder (or double-click it)")
        self.assign_button = ttk.Button(self.commands, text="Assign to clients…", style="Small.Secondary.TButton",
                                        command=self.assign_selected, state="disabled")
        kit.tooltip(self.assign_button, "Choose which client workspaces get the selected rule")
        self.preview_button = ttk.Button(self.commands, text="Preview and deploy…", style="Small.Secondary.TButton",
                                         command=self.preview_selected, state="disabled")
        kit.tooltip(self.preview_button, "Check the selected rule against a client workspace, then deploy it")
        for button in (self.edit_button, self.assign_button, self.preview_button):
            button.pack(side="left", padx=(0, px(6)))
        self.rule_actions = [self.assign_button, self.preview_button]
        self.table_frame, self.tree, self.empty = kit.table(body, COLUMNS, height=6, sort=self.sort_by, menu=self.row_menu)
        self.table_frame.pack(fill="both", expand=True)
        self.tree.sort_state = self.sort  # The library lists rules by name; the heading says so.
        self.empty.line.configure(width=px(160))  # The loading line's own width stays inside a narrow pane.
        fit_columns(self.tree, OPTIONAL)

    def build_detail(self):
        detail = self.detail_card = kit.Card(self.split, "Details", DETAIL_SUBTITLE, icon="document", tone="neutral")
        self.split.add(detail.frame, weight=2)
        self.detail_area, self.detail_empty = detail_pane(detail)
        inner = self.detail_area.inner
        self.facts = CopyValues(inner, key_width=12, copied=lambda key: self.app.flash(key + " copied."))
        self.assigned_label = kit.section_label(inner, "Assigned rules")
        self.assigned_frame, self.assigned, _ = kit.table(
            inner, (("who", "Rule", 80, True), ("state", "Effective state", 110, False), ("overrides", "Overrides", 110, False)),
            height=3)
        fit_columns(self.assigned, dict(overrides=360))
        self.assigned_none = tk.StringVar()
        self.none_label = ttk.Label(inner, textvariable=self.assigned_none, style="Hint.TLabel")
        self.state_hint = kit.wrap_to_parent(ttk.Label(
            inner, text="State comes from the rule's enabled setting and any override for this client; "
                        "Not specified is not Disabled.", style="Hint.TLabel", justify="left", wraplength=px(300)),
            minimum=px(120))
        self.raw = RawConfig(inner, self.copy_configuration, height=12)
        self.details = self.raw.text

    # ------------------------------------------------------------- source
    def github_repository(self):
        return self.app.github_repository()

    def auto_load(self):
        """Load the saved GitHub library the first time the view is opened."""
        if (not self.app.busy and self.snapshot is None and self.mode.get() == "GitHub repository"
                and self.source.get().strip()):
            self.load()

    def show_source(self, open_form=None):
        """The compact row once a library is loaded; the full form when no source is set, a folder is
        chosen but not read, the analyst asked to change the source, or reading it failed."""
        if open_form is not None: self.source_open = open_form
        local = self.mode.get() == "Local folder"
        full = (self.source_open or not self.source.get().strip() or (local and self.snapshot is None)
                or bool(self.error_banner.winfo_manager()))
        card = self.source_card
        if full:
            self.compact.pack_forget()
            if not self.form.winfo_manager(): self.form.pack(fill="x")
            card.subtitle_label.grid(); card.body.configure(padding=(px(20), px(16), px(20), px(20)))
        else:
            self.form.pack_forget()
            if not self.compact.winfo_manager(): self.compact.pack(fill="x")
            card.subtitle_label.grid_remove(); card.body.configure(padding=(px(20), px(4), px(20), px(14)))
        self.choose_button.pack_forget(); self.cancel_button.pack_forget()
        if local: self.choose_button.pack(side="left", padx=(px(8), 0))
        # Cancel goes back to the compact row while the form still describes the loaded library.
        if self.snapshot is not None: self.cancel_button.pack(side="left", padx=(px(4), 0))
        if open_form and full: self.source_entry.focus_set()

    def show_hint(self):
        if self.source_hint.get() and not self.hint_label.winfo_manager():
            self.hint_label.pack(fill="x", pady=(px(10), 0), before=self.error_banner if self.error_banner.winfo_manager() else None)
        elif not self.source_hint.get() and self.hint_label.winfo_manager():
            self.hint_label.pack_forget()

    def reading(self, text):
        """Loading progress, in the source card and over the list."""
        self.status.set(text); self.source_hint.set(text); self.empty.text.set(text)

    def failed(self, error):
        """The library couldn't be read: say why in the source card, with the way to fix it."""
        github = self.mode.get() == "GitHub repository"
        # The advice already says what went wrong; the generic opening is only for a message it doesn't know.
        tip = lookup(error, repo=self.source.get().strip() if github else None)
        self.error.set(tip or "The rule library couldn't be read. " + str(error))
        self.error_banner.set_action(*(("Go to Connect", self.app.show_github_settings) if github
                                       else ("Choose folder…", self.choose)))
        if not self.error_banner.winfo_manager(): self.error_banner.pack(fill="x", pady=(px(12), 0))
        self.source_hint.set("")
        self.show_source()
        self.empty.show("The rule library couldn't be read. Check the source above, then select Refresh library.")

    def hide_error(self):
        self.error.set(""); self.error_banner.pack_forget()

    def mode_changed(self, *args):
        local = self.mode.get() == "Local folder"
        self.source_label.configure(text="The folder that holds the rule library" if local
                                    else "GitHub repository, as organization/name")
        github = self.github_repository()
        self.source.set("" if local or "/" not in github else github)

    def invalidate(self, *args):
        self.snapshot = None; self.hide_error(); self.note.set("")
        if self.form.winfo_manager(): self.source_open = True  # Keep the form open while the analyst edits it.
        source = self.source.get().strip()
        local = self.mode.get() == "Local folder"
        self.source_hint.set("Select Refresh library to load this source." if source and local else "")
        self.status.set(source + " · not read yet. Select Refresh library to read it." if source else "")
        self.render(); self.show_source()

    def choose(self):
        if self.app.busy: return
        folder = filedialog.askdirectory(parent=self.app.root, title="Choose the folder that holds the rule library")
        if folder:
            self.mode.set("Local folder"); self.source.set(folder); self.load()

    # ------------------------------------------------------------- commands
    def say(self, text, action=None, tone="info"):
        """Guidance about a command (why it can't run yet), above the command bar."""
        self.note.set(text)
        self.message.tone(tone); self.message.set_action(*(action or ()))

    def show_note(self):
        if self.note.get():
            if not self.message.winfo_manager():
                self.message.pack(fill="x", pady=(0, px(10)),
                                  before=self.commands if self.commands.winfo_manager() else self.table_frame)
        elif self.message.winfo_manager():
            self.message.pack_forget(); self.message.set_action(); self.message.tone("info")

    def open_github_rules(self):
        if self.app.busy: return
        repository = self.github_repository()
        if "/" not in repository:
            self.say("Set the rule library once on Connect, then come back here.",
                     ("Go to Connect", self.app.show_github_settings))
            return
        self.view.set("Rules")
        if self.mode.get() != "GitHub repository": self.mode.set("GitHub repository")
        elif not self.source.get().strip(): self.source.set(repository)
        if self.snapshot is None: self.load()
        else: self.say("Double-click the rule you want to change.")

    def open_row(self, event=None):
        """Double-click or Enter on a rule opens it for editing."""
        if event is not None and event.type == tk.EventType.ButtonPress and self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.view.get() == "Rules" and self.tree.selection(): self.edit_selected()

    def sync_actions(self):
        rules = self.view.get() == "Rules"
        if rules and not self.edit_button.winfo_manager():
            self.edit_button.pack(side="left", padx=(0, px(6)), before=self.assign_button)
        elif not rules and self.edit_button.winfo_manager(): self.edit_button.pack_forget()
        chosen = rules and self.snapshot is not None and bool(self.tree.selection()) and not self.app.busy
        for button in (self.edit_button, *self.rule_actions):
            button.configure(state="normal" if chosen else "disabled")

    def selected_rule(self):
        selected = self.tree.selection()
        if self.view.get() != "Rules" or not selected or not self.snapshot: return None
        return self.visible[int(selected[0])]

    def edit_selected(self):
        if self.app.busy: return
        row = self.selected_rule()
        if row is None:
            self.say("Choose Rules, then select the rule you want to edit."); return
        source = None
        if self.snapshot.get("repository"):
            source = {"repository": self.snapshot["repository"], "revision": self.snapshot["revision"], "path": row["path"]}
        if self.app.rule_builder.edit_rule(row["raw"], source=source):
            self.app.show_page("builder")

    def preview_selected(self):
        if self.app.busy: return
        row = self.selected_rule()
        if row is None:
            self.say("Choose Rules, then select the rule to preview."); return
        if not self.snapshot.get("repository") or not self.snapshot.get("private"):
            self.say("Previews need the library from your team's private GitHub repository. Choose GitHub as the source, "
                     "then select Refresh library.", ("Change source…", lambda: self.show_source(True)))
            return
        if self.snapshot["issues"]:
            self.say("Fix the library issues before starting a preview.", ("Library issues", self.issues), "warning"); return
        self.note.set(""); PreviewDialog(self.app, self.snapshot, row)

    def open_workflow_history(self):
        if not self.app.busy: WorkflowHistoryDialog(self.app)

    def assign_selected(self):
        if self.app.busy: return
        row = self.selected_rule()
        if row is None:
            self.say("Choose Rules, then select the rule to assign."); return
        if not self.snapshot.get("repository") or not self.snapshot.get("private") or not self.snapshot.get("can_push"):
            self.say("Assigning rules needs the library from your team's private GitHub repository, with write access. "
                     "Choose GitHub as the source, then select Refresh library.", ("Change source…", lambda: self.show_source(True)))
            return
        if self.snapshot["issues"]:
            self.say("Fix the library issues before assigning rules.", ("Library issues", self.issues), "warning"); return
        self.note.set(""); AssignmentDialog(self.app, self.snapshot, row)

    def open_assignment_review(self):
        if self.app.busy: return
        path = filedialog.askopenfilename(parent=self.app.root, title="Open saved assignment",
            initialdir=str(self.app.rule_builder.data_dir / "assignment-requests"),
            filetypes=[("Assignment review", "*.assignment.json")])
        if not path: return
        try:
            record = load_assignment_request(path)
            self.note.set(""); AssignmentDialog(self.app, record=record)
        except Exception as error:
            self.say("That saved assignment couldn't be opened: " + str(error), tone="danger")

    def library_menu(self):
        """The library-wide commands, under the Library ⋯ button."""
        if self.app.busy: return
        menu, count = self.menu, self.issue_count()
        menu.delete(0, "end")
        menu.add_command(label="Library issues" + (f" ({count})" if count else ""), command=self.issues,
                         state="normal" if self.snapshot else "disabled")
        menu.add_separator()
        menu.add_command(label="Open saved assignment…", command=self.open_assignment_review)
        menu.add_command(label="Preview & deployment history…", command=self.open_workflow_history)
        button = self.menu_button
        try: menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        finally: menu.grab_release()

    def issue_count(self):
        return len(self.snapshot["issues"]) if self.snapshot else 0

    def issues(self):
        if not self.snapshot: return
        problems = self.snapshot["issues"]
        if problems:
            kit.alert(self.app.root, "Library issues",
                      f"The library has {plural(len(problems), 'issue')}. Assigning and previewing rules stay unavailable "
                      "until they are fixed in the repository and the library is refreshed.",
                      details="\n".join(problems), tone="warning")
        else:
            kit.alert(self.app.root, "Library issues",
                      "No problems reading the library. Pipeline checks and live workspace checks have not run.",
                      tone="success")

    def copy_text(self, text, what):
        self.app.root.clipboard_clear(); self.app.root.clipboard_append(text)
        self.app.flash(what + " copied.")

    def copy_configuration(self):
        text = self.details.get("1.0", "end-1c")
        if not text.strip(): self.app.flash("Select a client or rule first.", "info", 3); return
        self.copy_text(text, "Configuration")

    def row_menu(self, iid):
        row = self.visible[int(iid)]
        copy_path = ("Copy file path", lambda: self.copy_text(row["path"], "File path"))
        if self.view.get() == "Rules":
            return [("Edit in Rule builder", self.edit_selected, not self.app.busy), None,
                    ("Copy name", lambda: self.copy_text(row["name"], "Rule name")),
                    ("Copy rule ID", lambda: self.copy_text(row.get("id", ""), "Rule ID"), bool(row.get("id"))),
                    copy_path]
        return [("Copy client name", lambda: self.copy_text(row["name"], "Client name")),
                ("Copy workspace name", lambda: self.copy_text(row.get("workspace", ""), "Workspace name")),
                copy_path]

    # ------------------------------------------------------------- list
    def empty_state(self):
        if self.mode.get() == "Local folder":
            return ("Choose the folder that holds your rule library.", ("Choose folder…", self.choose))
        if "/" not in self.github_repository():
            return ("Set the rule library once on Connect.", ("Go to Connect", self.app.show_github_settings))
        return ("Select Refresh library to read the rules from GitHub.", ("Refresh library", self.load))

    def index(self):
        """Each row's search text, built once per loaded library rather than on every keystroke."""
        snapshot = self.snapshot
        texts = {kind: [(" ".join(map(str, row_values(row))) + " " + row["path"]).casefold() for row in snapshot[kind]]
                 for kind in ("clients", "rules")}
        self.searchable = (snapshot, texts)

    def sort_value(self, key, row):
        """The value a column sorts by; None sorts last either way."""
        if key == "state": return STATE_ORDER.index(row["state"])
        if key == "info":
            return kit.SEVERITY_RANK.get(row["severity"]) if "severity" in row else (row.get("workspace") or "").casefold() or None
        if key == "path": return str(row.get("target", row["path"])).casefold()
        return row["name"].casefold()

    def sort_by(self, key, descending):
        self.sort = (key, descending)
        self.render()

    def ordered(self, rows, texts):
        key, descending = self.sort
        pairs = list(zip(rows, texts))  # The library lists rows by name already.
        filled = [pair for pair in pairs if self.sort_value(key, pair[0]) is not None]
        filled.sort(key=lambda pair: self.sort_value(key, pair[0]), reverse=descending)
        return filled + [pair for pair in pairs if self.sort_value(key, pair[0]) is None]

    def selected_path(self):
        selected = self.tree.selection()
        if not selected or int(selected[0]) >= len(self.visible): return None
        return self.visible[int(selected[0])]["path"]

    def render(self):
        self.render_soon.cancel()
        kind = self.view.get()
        kit.set_headings(self.tree, dict(zip(("name", "state", "info", "path"), HEADINGS[kind])))
        keep = self.selected_path()
        self.tree.delete(*self.tree.get_children()); self.visible = []
        for value in HEADINGS:
            count = len(self.snapshot[value.lower()]) if self.snapshot else None
            self.view_bar.set_label(value, value if count is None else f"{value} {count}")
        # The state filter and the rule commands do nothing without a library: their room goes to the list.
        self.place_filters.hide(self.snapshot is None); self.place_filters()
        if self.snapshot is None: self.commands.pack_forget()
        elif not self.commands.winfo_manager(): self.commands.pack(fill="x", pady=(0, px(10)), before=self.table_frame)
        if self.snapshot is None:
            self.summary_pill.set("Not loaded")
            self.summary_pill.configure(cursor=""); self.summary_pill.tooltip_text = ""
            if self.loading: self.empty.show(busy=True)  # Keeps the reading message and its progress.
            else: self.empty.show(*self.empty_state())
            self.show_row(None); self.sync_actions(); return
        if self.searchable[0] is not self.snapshot: self.index()
        needle = self.search.get().strip().casefold()
        for row, text in self.ordered(self.snapshot[kind.lower()], self.searchable[1][kind.lower()]):
            if self.filter.get() not in ("All states", row["state"]): continue
            if needle and needle not in text: continue
            self.tree.insert("", "end", iid=str(len(self.visible)), values=row_values(row),
                             tags=("muted",) if row["state"] != "Enabled" else ())
            self.visible.append(row)
        issues = len(self.snapshot["issues"])
        self.summary_pill.set(plural(issues, "issue") if issues else "Library loaded", "warning" if issues else "success")
        self.summary_pill.configure(cursor="hand2" if issues else "")
        self.summary_pill.tooltip_text = "Show the library issues" if issues else ""
        match = next((str(index) for index, row in enumerate(self.visible) if row["path"] == keep), None)
        if match is not None:
            self.tree.selection_set(match); self.tree.see(match)  # Keep the open details while the list is filtered.
        else:
            self.show_row(None)
        if self.visible: self.empty.hide()
        else: self.empty.show("Nothing matches the current search and state filter.",
                              ("Clear search and filter", lambda: (self.search.set(""), self.filter.set("All states"))))
        self.sync_actions()

    def selected(self, event=None):
        self.sync_actions()
        selected = self.tree.selection()
        row = self.visible[int(selected[0])] if selected and self.snapshot and int(selected[0]) < len(self.visible) else None
        if row is not None and row is not self.shown: self.note.set("")  # A new choice clears old guidance.
        self.show_row(row)

    # ------------------------------------------------------------- details
    def assignments(self, row):
        """(who, effective state, overrides) for each assignment of the selected client or rule."""
        entries = []
        if self.view.get() == "Clients":
            rules = {rule["path"]: rule for rule in self.snapshot["rules"]}
            for assignment in row["assignments"]:
                rule = rules.get(assignment["path"]); override = assignment.get("overrides", {})
                effective = override.get("enabled", rule["raw"].get("enabled") if rule else None)
                entries.append((rule["name"] if rule else "Unavailable rule: " + assignment["path"], state(effective),
                                overrides_text(override)))
            return entries
        for client in self.snapshot["clients"]:
            for assignment in client["assignments"]:
                if assignment["path"] != row["path"]: continue
                override = assignment.get("overrides", {})
                # A switched-off target deploys nothing, whatever the rule says; it reads as muted.
                who = client["target"] + ("" if client["state"] == "Enabled" else " (target " + client["state"].lower() + ")")
                entries.append((who, state(override.get("enabled", row["raw"].get("enabled"))), overrides_text(override)))
        return entries

    def show_row(self, row):
        """Show the selected client or rule in the details pane, or the empty state for None."""
        if row is not None and row is self.shown: return
        self.shown = row
        for part in (self.facts, self.assigned_label, self.assigned_frame, self.none_label, self.state_hint):
            part.pack_forget()
        self.raw.place(False)
        card = self.detail_card
        self.assigned.delete(*self.assigned.get_children())
        if row is None:
            card.title_var.set("Details"); card.subtitle_var.set(DETAIL_SUBTITLE)
            self.facts.set([]); kit.set_text(self.details, "")
            self.detail_empty.show("This is what the library says should be deployed. Live status is in Sentinel.")
            return
        self.detail_empty.hide()
        clients = self.view.get() == "Clients"
        entries = self.assignments(row)
        card.title_var.set(row["name"])
        card.subtitle_var.set(("Client workspace" if clients else "Rule") + " in the library, not the live state in Sentinel.")
        if clients:
            facts = [("File", row["path"]), ("State", row["state"]), ("Workspace", row.get("workspace", "")),
                     ("Target", row.get("target", "")), ("Assigned", plural(len(entries), "rule"))]
        else:
            facts = [("File", row["path"]), ("State", row["state"]), ("Severity", row.get("severity", "")),
                     ("Rule ID", row.get("id", "")), ("Assigned to", plural(len(entries), "client"))]
        self.facts.set([(key, value) for key, value in facts if value], mono=MONO_KEYS)
        self.facts.pack(fill="x")
        self.assigned_label.label.configure(text="Assigned rules" if clients else "Assigned to clients")
        self.assigned_label.pack(fill="x", pady=(kit.space("lg"), px(8)))
        if entries:
            kit.set_headings(self.assigned, {"who": "Rule" if clients else "Client"})
            for index, values in enumerate(entries):
                self.assigned.insert("", "end", iid=str(index), values=values,
                                     tags=() if values[1] == "Enabled" and "(target" not in values[0] else ("muted",))
            self.assigned.configure(height=max(1, min(len(entries), 6)))
            self.assigned_frame.pack(fill="x")
        else:
            self.assigned_none.set("No rules are assigned to this client." if clients
                                   else "No clients in the loaded library use this rule.")
            self.none_label.pack(fill="x")
        if clients: self.state_hint.pack(fill="x", pady=(px(8), 0))
        kit.set_text(self.details, json.dumps(row["raw"], indent=2, ensure_ascii=False, default=str))
        self.raw.place()
        self.detail_area.canvas.yview_moveto(0)

    # ------------------------------------------------------------- loading
    def load(self):
        if self.app.busy: return
        key = (self.mode.get(), self.source.get().strip())
        if not key[1]:
            self.snapshot = None; self.render(); self.show_source(); return
        self.snapshot = None; self.note.set(""); self.hide_error(); self.render()
        self.empty.show("Reading the rule library…", busy=True)
        self.reading("Reading the rule library…"); self.show_source()
        progress = self.app.file_progress(self.reading)
        self.loading = True
        def task():
            try:
                snapshot = load_local(key[1]) if key[0] == "Local folder" else GitHubReader().load(key[1], progress)
                return snapshot, None
            except Exception as error:
                return None, str(error)
        def done(result):
            self.loading = False
            if key != (self.mode.get(), self.source.get().strip()): return
            snapshot, error = result
            if error:
                self.render(); self.failed(error)
                self.app.status.set("Couldn't read the rule library."); return
            self.snapshot = snapshot; self.index(); self.source_open = False; self.render()
            self.loaded(snapshot, key[1] if key[0] == "Local folder" else snapshot["source"].split(" / ")[0])
            self.app.log("Library revision: " + str(snapshot["revision"]))
            self.app.flash("Rule library loaded.")
        self.app.work("Reading the rule library…", task, done, page="rules", failed="Couldn't read the rule library")

    def loaded(self, snapshot, where):
        """Fold the source card down to where the library came from and when."""
        identity = snapshot.get("identity")
        self.status.set(f"{where} · loaded {friendly_time(snapshot['loaded_at'])}"
                        + (f" as {identity}" if identity and identity != "Local folder" else ""))
        self.source_hint.set("")
        self.show_source()
