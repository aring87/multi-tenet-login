"""The rule library: a read-only view of the detection repository, separate from live Azure rules."""
import json
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog
from . import ui_kit as kit
from .desktop_theme import px
from .repository_catalog import GitHubReader, load_local, state
from .preview_dialog import PreviewDialog
from .assignment_dialog import AssignmentDialog
from .workflow_history_dialog import WorkflowHistoryDialog
from .rule_assignments import load_request as load_assignment_request
from .rules_page import STATES

HEADINGS = {"Clients": ("Client", "State", "Workspace", "Target"), "Rules": ("Rule", "State", "Severity", "File")}


def friendly_time(value):
    """A library's load time in local words: 'today at 14:02' or '12 Oct, 14:02'."""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
    except (ValueError, TypeError):
        return str(value or "")
    now = datetime.now().astimezone()
    return ("today at " if moment.date() == now.date() else moment.strftime("%d %b, ")) + moment.strftime("%H:%M")


class CatalogPage:
    def __init__(self, app, parent):
        self.app, self.snapshot, self.visible = app, None, []
        self.mode = tk.StringVar(value="Local folder")
        self.source = tk.StringVar()
        self.search = tk.StringVar()
        self.view = tk.StringVar(value="Clients")
        self.filter = tk.StringVar(value="All states")

        source = kit.Card(parent, "Library source", "Read the rule library from GitHub, or from a folder on this PC.",
                          icon="library")
        source.frame.pack(fill="x", pady=(0, kit.space("lg")))
        self.refresh_button = app.button(source.actions, "Refresh library", self.load)
        self.refresh_button.pack(side="right"); kit.tooltip(self.refresh_button, "Read the library again (F5)")
        body = source.body
        kit.Segmented(body, self.mode, (("GitHub repository", "GitHub"), ("Local folder", "Folder on this PC")),
                      enabled=lambda: not app.busy).pack(anchor="w", pady=(0, px(14)))
        self.source_label = tk.StringVar()
        head = ttk.Frame(body, style="Card.TFrame"); head.pack(fill="x", pady=(0, px(5)))
        ttk.Label(head, textvariable=self.source_label, style="Field.TLabel").pack(side="left")
        row = ttk.Frame(body, style="Card.TFrame"); row.pack(fill="x")
        entry = ttk.Entry(row, textvariable=self.source); entry.pack(side="left", fill="x", expand=True)
        app.controls.append((entry, "normal"))
        self.choose_button = app.button(row, "Choose folder…", self.choose)
        self.note = tk.StringVar()
        kit.wrap_to_parent(ttk.Label(body, textvariable=self.note, style="Hint.TLabel", justify="left", wraplength=px(600))).pack(
            fill="x", pady=(px(10), 0))

        library = kit.Card(parent, "Rule library", "What the library says should be deployed. Live status is in Sentinel.",
                           icon="rules")
        library.frame.pack(fill="both", expand=True, pady=(0, kit.space("lg")))
        self.summary_pill = kit.Pill(library.actions, "Not loaded")
        self.summary_pill.pack()
        body = library.body
        bar = ttk.Frame(body, style="Card.TFrame"); bar.pack(fill="x", pady=(0, px(12)))
        kit.Segmented(bar, self.view, (("Clients", "Clients"), ("Rules", "Rules")), compact=True).pack(side="left")
        kit.Segmented(bar, self.filter, STATES, compact=True).pack(side="right")
        self.search_entry = ttk.Entry(bar, textvariable=self.search)
        self.search_entry.pack(side="left", fill="x", expand=True, padx=px(12))
        app.controls.append((self.search_entry, "normal"))
        kit.placeholder(self.search_entry, self.search, "Search the library  (Ctrl+F)")
        self.summary = tk.StringVar(value="")
        kit.wrap_to_parent(ttk.Label(body, textvariable=self.summary, style="Hint.TLabel")).pack(fill="x", pady=(0, px(10)))
        frame, self.tree, self.empty = kit.table(body, (("name", "Client", 230, True), ("state", "State", 130, False),
                                                       ("info", "Workspace", 160, True), ("path", "Target", 260, True)),
                                                 height=10)
        frame.pack(fill="both", expand=True)
        # Editing is the most common action, so it sits right under the list.
        self.edit_bar = ttk.Frame(body, style="Card.TFrame")
        self.edit_button = ttk.Button(self.edit_bar, text="Edit selected rule", style="Primary.TButton",
                                      command=self.edit_selected, state="disabled")
        self.edit_button.pack(side="right")
        kit.wrap_to_parent(ttk.Label(self.edit_bar, text="Double-click a rule to edit it in the Rule builder.",
                                     style="Hint.TLabel", justify="left", width=1), minimum=px(120)).pack(
            side="left", fill="x", expand=True, padx=(0, px(12)))

        detail = kit.Card(parent, "Details", "Select a client or rule to see its assignments and configuration.",
                          icon="document", tone="neutral")
        detail.frame.pack(fill="both", expand=True)
        self.rule_actions = []
        for title, entries, gated in (
                ("Selected rule", (("Assign to clients…", self.assign_selected),
                                   ("Preview and deploy…", self.preview_selected)), True),
                ("Library", (("Library issues", self.issues), ("Open saved assignment…", self.open_assignment_review),
                             ("Preview & deployment history…", self.open_workflow_history)), False)):
            kit.section_label(detail.body, title)
            actions = ttk.Frame(detail.body, style="Card.TFrame"); actions.pack(fill="x", pady=(0, px(10)))
            actions.columnconfigure((0, 1, 2), weight=1, uniform="catalog")
            for index, (label, callback) in enumerate(entries):
                button = ttk.Button(actions, text=label, command=callback, style="Secondary.TButton")
                button.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else px(8), 0))
                if gated: self.rule_actions.append(button)
                else: app.controls.append((button, "normal"))
        frame, self.details = kit.text_area(detail.body, 12, readonly=True)
        frame.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.selected)
        self.tree.bind("<Double-1>", self.open_row)
        self.tree.bind("<Return>", self.open_row)
        self.mode.trace_add("write", self.mode_changed)
        self.source.trace_add("write", self.invalidate)
        for var in (self.search, self.view, self.filter):
            var.trace_add("write", lambda *args: self.render())
        if "/" in self.github_repository(): self.mode.set("GitHub repository")
        else: self.mode_changed()
        self.render()

    def github_repository(self):
        return self.app.github_repository()

    def auto_load(self):
        """Load the saved GitHub library the first time the view is opened."""
        if (not self.app.busy and self.snapshot is None and self.mode.get() == "GitHub repository"
                and self.source.get().strip()):
            self.load()

    def open_github_rules(self):
        if self.app.busy: return
        repository = self.github_repository()
        if "/" not in repository:
            self.note.set("Set the rule library once on Connect, then come back here.")
            return
        self.view.set("Rules")
        if self.mode.get() != "GitHub repository": self.mode.set("GitHub repository")
        elif not self.source.get().strip(): self.source.set(repository)
        if self.snapshot is None: self.load()
        else: self.note.set("Double-click the rule you want to change.")

    def open_row(self, event=None):
        """Double-click or Enter on a rule opens it for editing."""
        if event is not None and event.type == tk.EventType.ButtonPress and self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.view.get() == "Rules" and self.tree.selection(): self.edit_selected()

    def sync_actions(self):
        rules = self.view.get() == "Rules" and self.snapshot is not None
        if rules and not self.edit_bar.winfo_manager(): self.edit_bar.pack(fill="x", pady=(px(12), 0))
        elif not rules and self.edit_bar.winfo_manager(): self.edit_bar.pack_forget()
        chosen = rules and bool(self.tree.selection()) and not self.app.busy
        self.edit_button.configure(state="normal" if chosen else "disabled")
        remote = chosen and bool(self.snapshot.get("repository")) and bool(self.snapshot.get("private"))
        for button in self.rule_actions:
            button.configure(state="normal" if remote else "disabled")

    def selected_rule(self):
        selected = self.tree.selection()
        if self.view.get() != "Rules" or not selected or not self.snapshot: return None
        return self.visible[int(selected[0])]

    def edit_selected(self):
        if self.app.busy: return
        row = self.selected_rule()
        if row is None:
            self.note.set("Choose Rules, then select the rule you want to edit."); return
        source = None
        if self.snapshot.get("repository"):
            source = {"repository": self.snapshot["repository"], "revision": self.snapshot["revision"], "path": row["path"]}
        if self.app.rule_builder.edit_rule(row["raw"], source=source):
            self.app.show_page("builder")

    def preview_selected(self):
        if self.app.busy: return
        row = self.selected_rule()
        if row is None:
            self.note.set("Choose Rules, then select the rule to preview."); return
        if not self.snapshot.get("repository") or not self.snapshot.get("private"):
            self.note.set("Previews need the library loaded from your team's private GitHub repository. Choose GitHub as the "
                          "library source, select Refresh library, then try again."); return
        if self.snapshot["issues"]:
            self.note.set("Fix the library issues before starting a preview."); return
        PreviewDialog(self.app, self.snapshot, row)

    def open_workflow_history(self):
        if not self.app.busy: WorkflowHistoryDialog(self.app)

    def assign_selected(self):
        if self.app.busy: return
        row = self.selected_rule()
        if row is None:
            self.note.set("Choose Rules, then select the rule to assign."); return
        if not self.snapshot.get("repository") or not self.snapshot.get("private") or not self.snapshot.get("can_push"):
            self.note.set("Assigning rules needs the library loaded from your team's private GitHub repository with write "
                          "access. Choose GitHub as the library source, select Refresh library, then try again."); return
        if self.snapshot["issues"]:
            self.note.set("Fix the library issues before assigning rules."); return
        AssignmentDialog(self.app, self.snapshot, row)

    def open_assignment_review(self):
        if self.app.busy: return
        path = filedialog.askopenfilename(parent=self.app.root, title="Open saved assignment",
            initialdir=str(self.app.rule_builder.data_dir / "assignment-requests"),
            filetypes=[("Assignment review", "*.assignment.json")])
        if not path: return
        try:
            AssignmentDialog(self.app, record=load_assignment_request(path))
        except Exception as error:
            self.note.set("That saved assignment couldn't be opened: " + str(error))

    def mode_changed(self, *args):
        local = self.mode.get() == "Local folder"
        self.source_label.set("Folder with the rule library" if local else "GitHub repository (organization/name)")
        if local: self.choose_button.pack(side="left", padx=(px(8), 0))
        else: self.choose_button.pack_forget()
        github = self.github_repository()
        self.source.set("" if local or "/" not in github else github)

    def invalidate(self, *args):
        self.snapshot = None
        self.note.set("Select Refresh library to load this source." if self.source.get().strip() else "")
        self.render()

    def choose(self):
        if self.app.busy: return
        folder = filedialog.askdirectory(parent=self.app.root, title="Choose the folder that holds the rule library")
        if folder:
            self.mode.set("Local folder"); self.source.set(folder); self.load()

    def detail(self, text):
        kit.set_text(self.details, text)

    def empty_state(self):
        if self.mode.get() == "Local folder":
            return ("Choose the folder that holds your rule library.", ("Choose folder…", self.choose))
        if "/" not in self.github_repository():
            return ("Set the rule library once on Connect.", ("Go to Connect", self.app.show_github_settings))
        return ("Select Refresh library to read the rules from GitHub.", ("Refresh library", self.load))

    def render(self):
        self.tree.delete(*self.tree.get_children()); self.detail(""); self.visible = []
        kind = self.view.get()
        for key, heading in zip(("name", "state", "info", "path"), HEADINGS[kind]):
            self.tree.heading(key, text=heading)
        if self.snapshot is None:
            self.summary.set(""); self.summary_pill.set("Not loaded")
            self.empty.show(*self.empty_state()); self.sync_actions(); return
        needle = self.search.get().strip().casefold()
        for row in self.snapshot[kind.lower()]:
            values = (row["name"], row["state"], row.get("workspace", row.get("severity")), row.get("target", row["path"]))
            if self.filter.get() not in ("All states", row["state"]): continue
            if needle and needle not in (" ".join(values) + " " + row["path"]).casefold(): continue
            self.tree.insert("", "end", iid=str(len(self.visible)), values=values,
                             tags=("muted",) if row["state"] != "Enabled" else ())
            self.visible.append(row)
        issues = len(self.snapshot["issues"])
        clients, rules = len(self.snapshot["clients"]), len(self.snapshot["rules"])
        self.summary.set(f"{clients} client workspace{'s' if clients != 1 else ''} · {rules} rule{'s' if rules != 1 else ''}"
                         + (f" · {issues} issue{'s' if issues != 1 else ''}" if issues else "") + f" · {len(self.visible)} shown")
        self.summary_pill.set(f"{issues} issue{'s' if issues != 1 else ''}" if issues else "Library loaded",
                              "warning" if issues else "success")
        if self.visible: self.empty.hide()
        else: self.empty.show("Nothing matches the current search and state filter.",
                              ("Clear search and filter", lambda: (self.search.set(""), self.filter.set("All states"))))
        self.sync_actions()

    def selected(self, event=None):
        self.sync_actions()
        selected = self.tree.selection()
        if not selected or not self.snapshot: return
        row = self.visible[int(selected[0])]
        lines = ["Library configuration only — not the live state in Sentinel.", "File: " + row["path"], ""]
        if self.view.get() == "Clients":
            rules = {r["path"]: r for r in self.snapshot["rules"]}
            lines.append("Assigned rules:")
            for assignment in row["assignments"]:
                rule = rules.get(assignment["path"])
                override = assignment.get("overrides", {})
                effective = override.get("enabled", rule["raw"].get("enabled") if rule else None)
                lines.append(f"• {rule['name'] if rule else 'Unavailable rule'} | {state(effective)} | {assignment['path']}")
            if not row["assignments"]: lines.append("No rules are assigned to this client.")
            lines.append("State comes from the rule's enabled setting and any override for this client; Not specified is not Disabled.")
        else:
            lines.append("Assigned to:")
            targets = [c for c in self.snapshot["clients"] if any(a["path"] == row["path"] for a in c["assignments"])]
            lines.extend(c["target"] + " (target " + c["state"].lower() + ")" for c in targets)
            if not targets: lines.append("No clients in the loaded library.")
        lines.extend(["", "Configuration", json.dumps(row["raw"], indent=2, ensure_ascii=False, default=str)])
        self.detail("\n".join(lines))

    def issues(self):
        if self.snapshot:
            self.detail("\n".join(self.snapshot["issues"]) or
                        "No problems reading the library. Pipeline checks and live workspace checks have not run.")

    def load(self):
        if self.app.busy: return
        key = (self.mode.get(), self.source.get().strip())
        if not key[1]:
            self.snapshot = None; self.render(); return
        self.snapshot = None; self.render(); self.note.set("Reading the rule library…")
        self.empty.show("Reading the rule library…")
        progress = self.app.file_progress(self.note.set)
        def task():
            try:
                snapshot = load_local(key[1]) if key[0] == "Local folder" else GitHubReader().load(key[1], progress)
                return snapshot, None
            except Exception as error:
                return None, str(error)
        def done(result):
            if key != (self.mode.get(), self.source.get().strip()): return
            snapshot, error = result
            if error:
                self.note.set("The rule library is unavailable: " + error)
                self.render(); self.empty.show("The rule library couldn't be read. Check the source, then select Refresh library.",
                                               ("Refresh library", self.load))
                self.app.status.set("Couldn't read the rule library."); return
            self.snapshot = snapshot; self.render()
            where = snapshot["source"].split(" / ")[0] if key[0] != "Local folder" else key[1]
            self.note.set(f"Loaded from {where} {friendly_time(snapshot['loaded_at'])}"
                          + (f" as {snapshot['identity']}" if snapshot.get("identity") else "")
                          + ". Select Refresh library to get the latest."
                          + ("\nDouble-click the rule you want to change." if self.view.get() == "Rules" else ""))
            self.app.log("Library revision: " + str(snapshot["revision"]))
            self.app.flash("Rule library loaded.")
        self.app.work("Reading the rule library…", task, done, page="rules")
