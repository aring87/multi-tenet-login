"""Drift view: the rule library's plan for the connected client beside the rules live in its workspace.

Read-only. It compares what the Workspace rules and Rule library views have already read, and starts
no read of its own: Refresh and Refresh library run those views' own loaders. Its actions open the
Rule library's own dialogs (assign, preview and deploy) for the connected client, which go through
review as they do from there."""
import json
import tkinter as tk
from tkinter import ttk
from . import ui_kit as kit
from .assignment_dialog import AssignmentDialog
from .catalog_page import overrides_text, plural
from .preview_dialog import PreviewDialog
from . import live_rules, rule_schema
from .rule_assignments import MAX_RULES
from .workspace_tools import read_rule
from .desktop_theme import COLORS, px
from .repository_catalog import state
from .rule_drift import drift, key, DIFFERENT, LIVE_ONLY, MISSING
from .rules_page import (CopyValues, RawConfig, Split, SEVERITY_TONES, detail_pane, fit_columns, flow, friendly_stamp,
                         keep_in_view, link_search)
from .workspace_tools import rule_values

KINDS = (("all", "All"), (MISSING, "Missing"), (DIFFERENT, "Different"), (LIVE_ONLY, "Only in workspace"))
KIND_LABELS = {MISSING: "Missing", DIFFERENT: "Different", LIVE_ONLY: "Only in workspace"}
KIND_TONES = {MISSING: "danger", DIFFERENT: "warning", LIVE_ONLY: "info"}
# Narrow starting widths that stretch, as in the workspace list (see rules_page.COLUMNS).
COLUMNS = (("name", "Rule name", 100, True), ("drift", "Drift", 150, False), ("library", "Library", 120, False),
           ("live", "Workspace", 120, False))
OPTIONAL = dict(library=540, live=540)  # Both sides join the list once it is this wide (96-DPI px).
SUBTITLE = "What the rule library assigns to the connected client, against the rules live in its workspace."
DETAIL_SUBTITLE = "Select a rule to see the library and the workspace side by side."
MONO_KEYS = {"Rule ID", "File", "Target"}
NONE = "—"


# The Drift key: each wording the Drift column uses, its dot, what it means and the step that fixes it.
# The labels are drift_text()'s own, so the key reads exactly as the list does.
KEY = (
    (MISSING, ("Missing from workspace",),
     "The library assigns this rule to the client, but it isn't in the workspace: it was never deployed, or was "
     "deleted in the portal.",
     "Preview and deploy… puts it in the workspace."),
    (DIFFERENT, ("State differs", "Severity differs", "State and severity differ"),
     "The rule is in the library and in the workspace, but the workspace's enabled state or severity doesn't match "
     "what the library sets for this client (after its overrides). Someone changed it in the portal, or the library "
     "changed and wasn't deployed.",
     "Preview and deploy… puts the library's settings back."),
    (LIVE_ONLY, ("Not assigned here",),
     "The rule is in the workspace and in the library, but the library doesn't assign it to this client, so GitHub "
     "doesn't manage it here.",
     "Assign to this client… adds it to the client's file."),
    (LIVE_ONLY, ("Not in library",),
     "The rule is only in the workspace: the library has no rule with its ID. It was made in the portal or turned on "
     "from Content hub.",
     "Add to library… opens it in the Rule builder as a new rule. Fusion and Microsoft incident-creation rules have "
     "no query of their own, so they stay in the portal."),
)
KEY_NOTE = ("A rule the library assigns to this client whose state and severity match isn't listed: it is in step, "
            "and the line under Rule drift counts those rules. Only the enabled state and severity are compared; "
            "other settings, such as the query or schedule, aren't counted as drift.")
FILTER_TIPS = {MISSING: "Assigned to this client in the library, but not in the workspace",
               DIFFERENT: "In both, but the workspace's enabled state or severity doesn't match the library",
               LIVE_ONLY: "In the workspace, but not in the library or not assigned to this client"}


class DriftKey:
    """What each kind of drift means, beside the dot the list shows it with."""

    def __init__(self, app):
        self.window = window = tk.Toplevel(app.root)
        window.title("Drift key"); window.configure(bg=COLORS["card"])
        body = ttk.Frame(window, style="Card.TFrame", padding=(kit.space("xl"), px(20), kit.space("xl"), px(18)))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="What the drift means", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(body, text="Each rule in the Drift list is out of step with the library in one of these ways.",
                  style="CardSubtitle.TLabel").pack(anchor="w", pady=(px(2), px(14)))
        rows = ttk.Frame(body, style="Card.TFrame"); rows.pack(fill="x")
        rows.columnconfigure(1, weight=1)
        wrap = px(520)
        self.labels = []
        for index, (kind, labels, meaning, step) in enumerate(KEY):
            dot = ttk.Label(rows, image=kit.dot(KIND_TONES[kind], rows), style="Card.TLabel")
            dot.grid(row=2 * index, column=0, sticky="nw", padx=(0, px(10)), pady=(px(10) if index else 0, 0))
            head = ttk.Label(rows, text=" · ".join(labels), style="Section.TLabel")
            head.grid(row=2 * index, column=1, sticky="w", pady=(px(8) if index else 0, 0))
            text = ttk.Label(rows, text=meaning + " " + step, style="Body.TLabel", wraplength=wrap, justify="left")
            text.grid(row=2 * index + 1, column=1, sticky="w", pady=(px(2), 0))
            self.labels.append((labels, text))
        ttk.Label(body, text=KEY_NOTE, style="Hint.TLabel", wraplength=wrap + px(24), justify="left").pack(
            anchor="w", pady=(px(16), 0))
        actions, buttons = kit.action_row(body, primary=("OK", window.destroy))
        actions.pack(fill="x", pady=(px(18), 0))
        self.ok_button = buttons[0]
        kit.place_dialog(window, app.root, width=640)
        kit.modal(window, app.root, window.destroy, primary=self.ok_button, focus=self.ok_button)


def side_text(state_text, severity):
    return state_text + " · " + (severity or "Not specified")


def drift_text(entry):
    """The Drift column: what is out of step, in a few words."""
    if entry["kind"] == MISSING: return "Missing from workspace"
    if entry["kind"] == LIVE_ONLY: return "Not assigned here" if entry["rule"] else "Not in library"
    fields = [field for field, *_ in entry["differences"]]
    return "State and severity differ" if len(fields) > 1 else fields[0] + " differs"


def row_values(entry):
    effective, live = entry["effective"], entry["live"]
    library = NONE
    if effective is not None and entry["kind"] != LIVE_ONLY:  # A library rule not assigned here sets nothing.
        library = side_text(state(effective.get("enabled")), effective.get("severity"))
    workspace = side_text(*rule_values(live)[1:3]) if live else NONE
    return (entry["name"], drift_text(entry), library, workspace)


def comparison(entry):
    """(setting, library, workspace, differs) rows for the details; a side that is absent reads '—'."""
    effective, live = entry["effective"], entry["live"]
    props = (live or {}).get("properties", {})
    duration = lambda value: kit.duration_text(value) if value else None
    rows = [("Name", effective.get("name") if effective else None, props.get("displayName") if live else None),
            ("State", state(effective.get("enabled")) if effective else None, rule_values(live)[1] if live else None),
            ("Severity", effective.get("severity") if effective else None, props.get("severity") if live else None),
            ("Type", effective.get("kind", "Scheduled") if effective else None, live.get("kind") if live else None),
            ("Runs every", duration(effective.get("queryFrequency")) if effective else None, duration(props.get("queryFrequency"))),
            ("Looks back", duration(effective.get("queryPeriod")) if effective else None, duration(props.get("queryPeriod")))]
    result = []
    for setting, library, workspace in rows:
        if library in (None, "") and workspace in (None, ""): continue
        differs = library not in (None, "") and workspace not in (None, "") and key(library) != key(workspace)
        if setting == "Name" and library and workspace and not differs: continue  # The title already names it.
        result.append((setting, str(library) if library not in (None, "") else NONE,
                       str(workspace) if workspace not in (None, "") else NONE, differs))
    return result


class DriftPage:
    def __init__(self, app, parent):
        self.app = app
        self.result = None  # The last comparison (rule_drift.drift), or None while it can't run.
        self.blocked = None  # Why it can't run: (message, action, busy).
        self.entries, self.cache = [], []  # Drifted rules, and (values, search text) for each.
        self.shown = -1  # Index of the entry in the details pane: None for none, -1 until it is drawn.
        self.current = None  # The rule ID in the details pane, kept while the rules are compared again.
        self.want_library = False  # Opening the view reads the saved GitHub library once, as the library view does.
        self.split = Split(parent, app.ui_state.get("drift_split", 0.6))
        self.split.pack(fill="both", expand=True)

        card = self.table_card = kit.Card(self.split, "Rule drift", SUBTITLE, icon="rules")
        self.split.add(card.frame, weight=3)
        self.summary = card.subtitle_var
        # Not registered in app.controls: sync() decides when these are usable.
        self.refresh_button = ttk.Button(card.actions, text="Refresh", command=self.refresh, style="Primary.TButton")
        self.refresh_button.pack(side="right")
        kit.tooltip(self.refresh_button, "Read the workspace rules from Sentinel again (F5)")
        self.library_button = ttk.Button(card.actions, text="Refresh library", command=self.refresh_library,
                                         style="Secondary.TButton")
        self.library_button.pack(side="right", padx=(0, px(8)))
        kit.tooltip(self.library_button, "Read the rule library again")
        # Always usable: it only explains the list.
        self.key_button = ttk.Button(card.actions, text="Key", command=self.show_key, style="Ghost.TButton")
        self.key_button.pack(side="right", padx=(0, px(8)))
        kit.tooltip(self.key_button, "What each kind of drift means, and the step that fixes it")
        body = card.body
        bar = ttk.Frame(body, style="Card.TFrame"); bar.pack(fill="x", pady=(0, px(12)))
        bar.columnconfigure(0, weight=1)
        self.search, self.filter = tk.StringVar(), tk.StringVar(value="all")
        self.search_entry = ttk.Entry(bar, textvariable=self.search)
        self.search_entry.grid(row=0, column=0, sticky="ew")
        app.inputs.append(self.search_entry)  # Searching stays possible while a task runs.
        kit.placeholder(self.search_entry, self.search, "Search drift  (Ctrl+F)", search=True)
        self.filter_bar = kit.Segmented(bar, self.filter, KINDS, compact=True)
        for kind, tip in FILTER_TIPS.items(): kit.tooltip(self.filter_bar.items[kind], tip)
        self.place_filters = flow(bar, self.filter_bar, 1)
        self.notice = tk.StringVar()
        self.banner = kit.Banner(body, self.notice, "warning")
        # Ctrl- or Shift-click selects several rules, to assign them to this client together.
        self.table_frame, self.tree, self.empty = kit.table(body, COLUMNS, height=6, sort=True, menu=self.row_menu,
                                                            marker=True, selectmode="extended")
        self.table_frame.pack(fill="both", expand=True)
        self.empty.line.configure(width=px(160))  # The loading line's own width stays inside a narrow pane.
        fit_columns(self.tree, OPTIONAL)
        self.build_detail()
        self.render_soon = kit.debounce(self.tree, 150, self.render)
        link_search(self.search_entry, self.tree, self.render_soon)
        self.tree.bind("<<TreeviewSelect>>", self.selected)
        self.tree.bind("<Return>", self.open_row)
        self.search.trace_add("write", self.render_soon)
        self.filter.trace_add("write", lambda *args: self.render())
        self.update()

    def build_detail(self):
        detail = self.detail_card = kit.Card(self.split, "Drift details", DETAIL_SUBTITLE, icon="document", tone="neutral")
        self.split.add(detail.frame, weight=2)
        self.detail_area, self.detail_empty = detail_pane(detail)
        inner = self.detail_area.inner
        self.pills = ttk.Frame(inner, style="Card.TFrame")
        self.explanation = tk.StringVar()
        self.explanation_label = kit.wrap_to_parent(ttk.Label(inner, textvariable=self.explanation, style="Body.TLabel",
                                                              justify="left", wraplength=px(300)), minimum=px(120))
        self.actions = ttk.Frame(inner, style="Card.TFrame")
        self.action_button = ttk.Button(self.actions, style="Small.Primary.TButton", command=self.act)
        self.action_button.pack(side="left")
        kit.tooltip(self.action_button, "")  # Its text is set per rule (action_button.tooltip_text).
        self.compare_label = kit.section_label(inner, "Library and workspace")
        self.compare_frame, self.compare, _ = kit.table(
            inner, (("setting", "Setting", 90, False), ("library", "Library", 100, True), ("live", "Workspace", 100, True)),
            height=4)
        self.compare.tag_configure("differs", foreground=COLORS["danger"])
        fit_columns(self.compare, {})
        self.facts = CopyValues(inner, key_width=12, copied=lambda key: self.app.flash(key + " copied."))
        self.library_raw = RawConfig(inner, lambda: self.copy(self.library_raw.text, "Library configuration"), height=12,
                                     label="Library configuration")
        self.live_raw = RawConfig(inner, lambda: self.copy(self.live_raw.text, "Workspace configuration"), height=12,
                                  label="Workspace configuration")

    # ------------------------------------------------------------- state
    def visible(self):
        return self.app.rule_view.get() == "drift"

    def blocker(self):
        """Why the comparison can't run yet, as (message, action, busy), or None once it can."""
        app, rules, library = self.app, self.app.rules_page, self.app.catalog_page
        if not rules.ready():
            return ("Sign in and choose a workspace to compare its rules with the rule library.",
                    ("Go to Connect", lambda: app.show_page("connect")), False)
        name = app.workspace["workspace_name"]
        if rules.loading: return ("Reading the rules from " + name + "…", None, True)
        if not rules.current_collection():
            return ("Load the rules from " + name + " to compare them with the rule library.",
                    ("Load workspace rules", rules.load), False)
        if not rules.rows and rules.collection.get("status") not in ("collected", "no_records"):
            return ("The rules couldn't be read from " + name + ". Select Refresh to try again.", None, False)
        if library.loading: return ("Reading the rule library…", None, True)
        if library.snapshot is None:
            if library.error.get():
                return ("The rule library couldn't be read. Open the Rule library to see why.",
                        ("Open Rule library", lambda: app.rule_view.set("repository")), False)
            text, action = library.empty_state()
            return ("Load the rule library to compare it with " + name + ". " + text, action, False)
        return None

    def update(self):
        """Compare again: the workspace rules, the rule library or the workspace changed."""
        rules, library = self.app.rules_page, self.app.catalog_page
        self.blocked = self.blocker()
        self.result = None if self.blocked else drift(library.snapshot, rules.rows, rules.loaded_workspace)
        self.entries = self.result["entries"] if self.result else []
        self.cache, self.shown = [(values, " ".join(values + (entry["id"],)).casefold())
                                  for entry, values in ((entry, row_values(entry)) for entry in self.entries)], -1
        self.show_banner(); self.label_filters(); self.render()
        if library.snapshot is not None or library.error.get(): self.want_library = False
        elif self.want_library and self.visible() and not self.app.busy and not library.loading:
            self.want_library = False
            self.app.root.after_idle(library.auto_load)

    def opened(self):
        """The drift view was chosen: read the saved GitHub library if none is loaded, then compare."""
        self.want_library = True
        self.update()

    def sync(self):
        """Refresh needs a workspace; Refresh library needs a library source."""
        idle = not self.app.busy
        self.action_button.configure(state="normal" if idle else "disabled")
        self.refresh_button.configure(state="normal" if idle and self.app.rules_page.ready() else "disabled")
        self.library_button.configure(state="normal" if idle and self.app.catalog_page.source.get().strip() else "disabled")

    def show_key(self):
        return DriftKey(self.app)

    def refresh(self):
        if not self.app.busy: self.app.rules_page.load()

    def refresh_library(self):
        if not self.app.busy: self.app.catalog_page.load()

    def target(self):
        return self.result["client"]["target"] if self.result and self.result["status"] == "ok" else ""

    def workspace_name(self):
        return (self.app.rules_page.loaded_workspace or self.app.workspace or {}).get("workspace_name", "the workspace")

    def show_banner(self):
        """Warn above the list about anything that makes the comparison less than complete."""
        notes, result = [], self.result
        if result and result["status"] == "ok":
            client, target = result["client"], self.target()
            if client["state"] != "Enabled":
                notes.append(f"{target} is switched off in the library, so the pipeline doesn't deploy its rules. "
                             "This compares what it would deploy.")
            if self.app.rules_page.collection.get("status") not in ("collected", "no_records"):
                notes.append(f"Not every rule could be read from {self.workspace_name()}, so a rule listed as missing "
                             "may be there. Select Refresh to read them again.")
            if result["unavailable"]:
                count = len(result["unavailable"])
                notes.append(f"{plural(count, 'rule')} assigned to {target} {'is' if count == 1 else 'are'} not in the "
                             "library and left out. Library issues in the Rule library lists "
                             + ("it." if count == 1 else "them."))
        self.notice.set(" ".join(notes))
        if notes and not self.banner.winfo_manager(): self.banner.pack(fill="x", pady=(0, px(12)), before=self.table_frame)
        elif not notes: self.banner.pack_forget()

    def label_filters(self):
        """Show how many drifted rules each filter holds ('Missing 2'), or just the names."""
        counted = self.result is not None and self.result["status"] == "ok"
        for value, label in KINDS:
            count = len(self.entries) if value == "all" else sum(entry["kind"] == value for entry in self.entries)
            self.filter_bar.set_label(value, f"{label} {count}" if counted else label)
        self.place_filters()

    # ------------------------------------------------------------- list
    def render(self):
        self.render_soon.cancel()
        keep = self.current  # Kept while the list is filtered, and when the rules are compared again.
        self.tree.delete(*self.tree.get_children())
        needle, kind = self.search.get().strip().casefold(), self.filter.get()
        for index, (entry, (values, haystack)) in enumerate(zip(self.entries, self.cache)):
            if kind not in ("all", entry["kind"]): continue
            if needle and needle not in haystack: continue
            self.tree.insert("", "end", iid=str(index), values=values,
                             image=kit.dot(KIND_TONES[entry["kind"]], self.tree))
        kit.sort_items(self.tree)  # A heading the analyst sorted by stays sorted.
        match = next((iid for iid in self.tree.get_children() if self.entries[int(iid)]["id"] == keep), None)
        if match is not None:
            self.tree.selection_set(match); self.tree.see(match); self.show(int(match))
        else:
            self.show(None)
            if self.blocked: self.current = keep  # Read again: the rule comes back with the comparison.
        self.summarise()
        self.sync()

    def summarise(self):
        """The card subtitle and the message over an empty list."""
        name, result = self.workspace_name(), self.result
        if self.blocked:
            text, action, busy = self.blocked
            self.summary.set(SUBTITLE); self.empty.show(text, action, busy=busy); return
        if result["status"] == "no_target":
            self.summary.set(f"No library target deploys to {name}.")
            self.empty.show(f"No client target in the rule library deploys to {name}. Targets are matched by "
                            "workspace ID, or by subscription, resource group and workspace name.",
                            ("Open Rule library", lambda: self.app.rule_view.set("repository")))
            return
        if result["status"] == "ambiguous":
            targets = result["targets"]
            self.summary.set(f"{len(targets)} library targets deploy to {name}.")
            self.empty.show(f"{len(targets)} library targets deploy to {name}: {', '.join(targets)}. Change the library "
                            "so only one does, then select Refresh library.")
            return
        self.summary.set(f"{self.target()} in the library, against {name}  ·  "
                         f"{plural(result['in_sync'], 'rule')} in step")
        if self.tree.get_children(): self.empty.hide()
        elif self.entries:
            term, kind = self.search.get().strip(), self.filter.get()
            text = (f"No drift matches “{term}”" if term else "No drift") + \
                ("" if kind == "all" else f" with the {KIND_LABELS[kind]} filter") + "."
            self.empty.show(text, ("Clear search and filter", self.clear_filters))
        else:
            self.empty.show(f"No drift. The {plural(result['assigned'], 'rule')} the library assigns to {self.target()} "
                            f"match {name}, and {name} has no other rules.")

    def clear_filters(self):
        self.search.set(""); self.filter.set("all")

    def selected(self, event=None):
        selection = self.tree.selection()
        self.show(int(selection[0]) if selection else None)
        self.place_action()  # The details show the first rule; the step covers the whole selection.

    def selected_entries(self):
        return [self.entries[int(item)] for item in self.tree.selection() if int(item) < len(self.entries)]

    def row_menu(self, iid):
        entry = self.entries[int(iid)]
        chosen = self.selected_entries() or [entry]  # Right-clicking inside a selection keeps it.
        idle = not self.app.busy
        if len(chosen) > 1:
            assignable = [item for item in chosen if self.action(item) == "assign"]
            steps = [(f"Assign {len(assignable)} rules to this client…" if len(assignable) > 1 else "Assign to this client…",
                      lambda: self.assign(assignable), idle and bool(assignable)),
                     ("Preview and deploy…", lambda: None, False)]  # Previews run one rule at a time.
        else:
            action = self.action(entry)
            steps = [("Assign to this client…", lambda: self.assign(entry), idle and action == "assign"),
                     ("Preview and deploy…", lambda: self.preview(entry), idle and action == "deploy")]
            if action == "add": steps.insert(0, ("Add to library…", lambda: self.add(entry), idle))
        return [*steps, None,
                ("Copy name", lambda: self.copy_text(entry["name"], "Rule name")),
                ("Copy rule ID", lambda: self.copy_text(entry["id"], "Rule ID"), bool(entry["id"])), None,
                ("Show in Workspace rules", lambda: self.show_live(entry), entry["live"] is not None),
                ("Show in Rule library", lambda: self.show_library(entry), entry["rule"] is not None)]

    # ------------------------------------------------------------- actions
    @staticmethod
    def action(entry):
        """What would bring ``entry`` in step from here: 'assign' (a library rule not assigned to this
        client), 'deploy' (assigned, but missing or different live), 'add' (a live rule the library could
        hold but doesn't) or None (a live rule of a kind the library can't hold)."""
        if entry["kind"] in (MISSING, DIFFERENT): return "deploy"
        if entry["rule"] is not None: return "assign"
        return "add" if live_rules.why_not(entry["live"]) is None else None

    def act(self):
        if self.app.busy: return
        chosen = self.selected_entries()
        if len(chosen) > 1:
            assignable = [entry for entry in chosen if self.action(entry) == "assign"]
            if assignable: self.assign(assignable)
            return
        index = self.shown
        if index is None or index < 0: return
        entry = self.entries[index]
        {"assign": self.assign, "deploy": self.preview, "add": self.add}.get(self.action(entry), lambda entry: None)(entry)

    def place_action(self):
        """The step under the explanation: for the one rule shown, or Assign N rules for a selection of
        several (the ones not assigned here; the others are left out and the tip says so)."""
        chosen, entry = self.selected_entries(), self.entries[self.shown] if self.shown not in (None, -1) else None
        target, text, tip = self.target(), None, ""
        if len(chosen) > 1:
            assignable = [item for item in chosen if self.action(item) == "assign"]
            if assignable:
                text = f"Assign {len(assignable)} rules to this client…" if len(assignable) > 1 else "Assign to this client…"
                skipped = len(chosen) - len(assignable)
                tip = (f"Assign the selected rules that aren't assigned here to {target}, each at its state in the "
                       "workspace, in one request a teammate approves.")
                if skipped:
                    tip += f" {plural(skipped, 'selected rule')} with another kind of drift " + \
                           ("is" if skipped == 1 else "are") + " left out."
        elif entry is not None:
            action = self.action(entry)
            if action == "assign":
                text = "Assign to this client…"
                tip = f"Assign this rule to {target}. A teammate approves the change before anything deploys."
            elif action == "deploy":
                text = "Preview and deploy…"
                tip = f"Preview this rule for {target}, then deploy it once the preview passes."
            elif action == "add":
                text = "Add to library…"
                tip = ("Open this rule in the Rule builder as a new library rule, exactly as it is live. "
                       "Submitting it asks a teammate to approve it.")
        if text is None:
            self.actions.pack_forget(); return
        self.action_button.configure(text=text); self.action_button.tooltip_text = tip
        if not self.actions.winfo_manager():
            self.actions.pack(fill="x", pady=(0, px(12)), after=self.explanation_label)

    def blocked_by(self, problem, title):
        """Say why the library can't take this step, in a dialog: the reason belongs to the Rule library."""
        if problem is None: return False
        text, _, tone = problem
        kit.alert(self.app.root, title, text, tone=tone)
        return True

    def assign(self, entries):
        """The Rule library's assignment dialog for one rule (an entry) or several (a list), with this
        client ticked and each rule at its live state."""
        library = self.app.catalog_page
        entries = [entry for entry in (entries if isinstance(entries, list) else [entries]) if entry["rule"] is not None]
        if self.app.busy or not entries or not self.target(): return
        if len(entries) > MAX_RULES:
            kit.alert(self.app.root, "Assign to this client", f"Select up to {MAX_RULES} rules to assign together.",
                      tone="info"); return
        if self.blocked_by(library.assign_problem(), "Assign to this client"): return
        live = {entry["rule"]["path"]: (entry["live"] or {}).get("properties", {}).get("enabled") is True for entry in entries}
        if len(entries) == 1:
            AssignmentDialog(self.app, library.snapshot, entries[0]["rule"], preselect=(self.target(),),
                             enabled=next(iter(live.values())))
        else:
            AssignmentDialog(self.app, library.snapshot, [entry["rule"] for entry in entries],
                             preselect=(self.target(),), enabled=live)

    def add(self, entry):
        """Read this live rule again at the version the pipeline deploys at, convert it into a library rule
        proven to deploy back exactly as it is, and open it in the Rule builder as a new rule."""
        app, rules = self.app, self.app.rules_page
        if app.busy or entry["rule"] is not None or self.action(entry) != "add": return
        session, workspace = app.session, dict(rules.loaded_workspace or {})
        if session is None or not workspace or app.workspace != rules.loaded_workspace:
            kit.alert(app.root, "Add to library", "Select Refresh to read the workspace rules again, then try again.",
                      tone="info"); return
        rule_id, name = live_rules.rule_id(entry["live"]), entry["name"]
        def task():
            record = read_rule(session, workspace, rule_id, rule_schema.API_VERSION)
            try: return live_rules.convert(record)
            except live_rules.NotConvertible as error: return dict(refused=str(error))
        def done(result):
            if result.get("refused"):
                kit.alert(app.root, "Add to library", f"“{name}” can't be added to the library from here. "
                          + result["refused"], tone="warning"); return
            note = (f"From {workspace['workspace_name']}: {name}. It matches the live rule exactly"
                    + (" (" + " ".join(result["notes"]) + ")" if result["notes"] else "") + ". "
                    + ("Before you submit: " + " ".join(result["fixes"]) + " " if result["fixes"] else "")
                    + "Check it, then Submit for approval.")
            if app.rule_builder.edit_rule(result["rule"], source=None, note=note): app.show_page("builder")
        app.work(f"Reading “{name}” from {workspace['workspace_name']} to add it to the library…", task, done,
                 page="rules", failed="Couldn't read the rule from Sentinel")

    def preview(self, entry):
        """The Rule library's preview and deploy dialog for this rule, with this client ticked."""
        library = self.app.catalog_page
        if self.app.busy or entry["rule"] is None or not self.target(): return
        if self.blocked_by(library.preview_problem(), "Preview and deploy"): return
        PreviewDialog(self.app, library.snapshot, entry["rule"], preselect=(self.target(),))

    def show_live(self, entry):
        """Open the Workspace rules view on this rule."""
        page = self.app.rules_page
        self.app.rule_view.set("workspace"); page.filter.set("All states"); page.search.set(entry["id"]); page.render()
        self.pick(page.tree, page.selected)

    def show_library(self, entry):
        """Open the Rule library view on this rule's file."""
        page = self.app.catalog_page
        self.app.rule_view.set("repository"); page.view.set("Rules"); page.filter.set("All states")
        page.search.set(entry["rule"]["path"]); page.render()
        self.pick(page.tree, page.selected)

    @staticmethod
    def pick(tree, selected):
        rows = tree.get_children()
        if rows:
            tree.selection_set(rows[0]); tree.focus(rows[0]); tree.see(rows[0]); selected()

    def open_row(self, event=None):
        """Enter on a rule moves into its details, to the first configuration toggle."""
        if not self.tree.selection(): return None
        target = next((raw.button for raw in (self.library_raw, self.live_raw) if raw.row.winfo_manager()), None)
        if target is None: return None
        target.focus_set(); keep_in_view(self.detail_area, target)
        return "break"

    def copy(self, widget, what):
        text = widget.get("1.0", "end-1c")
        if not text.strip():
            self.app.flash("Select a rule first.", "info", 3); return
        self.copy_text(text, what)

    def copy_text(self, text, what):
        self.app.root.clipboard_clear(); self.app.root.clipboard_append(text)
        self.app.flash(what + " copied.")

    # ------------------------------------------------------------- details
    def show(self, index):
        """Show entry ``index`` (or nothing) in the details pane, unless it is already there."""
        if index == self.shown: return
        self.shown = index
        self.current = self.entries[index]["id"] if index is not None else None
        self.detail(self.entries[index] if index is not None else None)

    def explain(self, entry):
        target, name = self.target(), self.workspace_name()
        if entry["kind"] == MISSING:
            text = f"The library assigns this rule to {target}, but it isn't in {name}."
            if self.result["client"]["state"] != "Enabled": text += f" {target} is switched off, so nothing deploys it."
            return text
        if entry["kind"] == DIFFERENT:
            return f"{name} doesn't match the library for {target}: " + "; ".join(
                f"{field.lower()} is {live} in the workspace, {library} in the library"
                for field, library, live in entry["differences"]) + "."
        if entry["rule"]:
            return f"This rule is in {name} and in the library, but the library doesn't assign it to {target}."
        text = f"This rule is in {name} but not in the rule library, so the library doesn't manage it."
        reason = live_rules.why_not(entry["live"])
        return text + (" " + reason if reason else " Add to library opens it in the Rule builder as a new rule.")

    def detail(self, entry=None):
        for child in self.pills.winfo_children(): child.destroy()
        for part in (self.pills, self.explanation_label, self.actions, self.compare_label, self.compare_frame, self.facts):
            part.pack_forget()
        self.library_raw.place(False); self.live_raw.place(False)  # Repacked last, so the parts keep their order.
        self.compare.delete(*self.compare.get_children())
        card = self.detail_card
        if entry is None:
            card.title_var.set("Drift details"); card.subtitle_var.set(DETAIL_SUBTITLE)
            self.explanation.set(""); self.facts.set([])
            kit.set_text(self.library_raw.text, ""); kit.set_text(self.live_raw.text, "")
            how = self.result["how"] if self.result and self.result["status"] == "ok" else None
            self.detail_empty.show((f"{self.target()} matches this workspace by {how}. " if how else "")
                                   + "The library's settings and the workspace's appear here side by side.")
            return
        self.detail_empty.hide()
        live, effective = entry["live"], entry["effective"]
        card.title_var.set(entry["name"])
        card.subtitle_var.set({MISSING: "In the library, not in the workspace", DIFFERENT: "In both, with different settings",
                               LIVE_ONLY: "In the workspace only"}[entry["kind"]])
        kit.Pill(self.pills, KIND_LABELS[entry["kind"]], KIND_TONES[entry["kind"]]).pack(side="left")
        severity = (live or {}).get("properties", {}).get("severity") or (effective or {}).get("severity")
        if severity in SEVERITY_TONES:
            kit.Pill(self.pills, severity, SEVERITY_TONES[severity], dot=False).pack(side="left", padx=(px(6), 0))
        self.pills.pack(fill="x", pady=(0, px(12)))
        self.explanation.set(self.explain(entry))
        self.explanation_label.pack(fill="x", pady=(0, px(12)))
        self.place_action()
        rows = comparison(entry)
        kit.set_headings(self.compare, {"library": "Library" if entry["kind"] != LIVE_ONLY or not entry["rule"]
                                        else "Library (not assigned)"})
        for index, (setting, library, workspace, differs) in enumerate(rows):
            self.compare.insert("", "end", iid=str(index), values=(setting, library, workspace),
                                tags=("differs",) if differs else ())
        self.compare.configure(height=max(1, len(rows)))
        self.compare_label.pack(fill="x", pady=(0, px(8)))
        self.compare_frame.pack(fill="x")
        props = (live or {}).get("properties", {})
        facts = [("Rule ID", entry["id"]), ("File", entry["rule"]["path"] if entry["rule"] else None),
                 ("Target", self.target() if entry["kind"] != LIVE_ONLY else None),
                 ("Overrides", overrides_text(entry["overrides"]) if entry["overrides"] else None),
                 ("Last modified", friendly_stamp(props["lastModifiedUtc"]) if props.get("lastModifiedUtc") else None)]
        self.facts.set([(key_, value) for key_, value in facts if value], mono=MONO_KEYS)
        self.facts.pack(fill="x", pady=(kit.space("lg"), 0))
        kit.set_text(self.library_raw.text, json.dumps(effective, indent=2, ensure_ascii=False, default=str)
                     if effective is not None else "")
        kit.set_text(self.live_raw.text, json.dumps(live, indent=2, ensure_ascii=False) if live else "")
        if effective is not None: self.library_raw.place()
        if live: self.live_raw.place()
        self.detail_area.canvas.yview_moveto(0)
