"""Read-only repository browser, separate from live Azure analytics rules."""
import json
import tkinter as tk
from tkinter import ttk, filedialog
from repository_catalog import GitHubReader, load_local, state
from preview_dialog import PreviewDialog


class CatalogPage:
    def __init__(self, app, parent):
        self.app, self.snapshot, self.visible = app, None, []
        card, body = app.card(parent, "Repository catalog", "Browse configured clients and rules. Deployment status must be checked separately in Sentinel.")
        card.pack(fill="both", expand=True, pady=(0, 16))
        self.mode = tk.StringVar(value="Local folder")
        self.source = tk.StringVar()
        self.search = tk.StringVar()
        self.view = tk.StringVar(value="Clients")
        self.filter = tk.StringVar(value="All states")
        top = ttk.Frame(body, style="Card.TFrame"); top.pack(fill="x")
        combo = ttk.Combobox(top, textvariable=self.mode, values=("Local folder", "GitHub repository"), state="readonly", width=18)
        combo.pack(side="left", padx=(0, 8)); app.controls.append((combo, "readonly"))
        app.button(top, "Choose folder", self.choose).pack(side="left")
        app.button(top, "Refresh catalog", self.load, "Primary.TButton").pack(side="right")
        self.source_label = tk.StringVar(value="Detection repository folder")
        ttk.Label(body, textvariable=self.source_label, style="Field.TLabel").pack(anchor="w", pady=(12, 5))
        entry = ttk.Entry(body, textvariable=self.source); entry.pack(fill="x"); app.controls.append((entry, "normal"))
        self.note = tk.StringVar(value="Choose a repository folder. GitHub mode uses your existing GitHub CLI sign-in for this pilot.")
        note = ttk.Label(body, textvariable=self.note, style="Muted.TLabel", wraplength=650)
        note.pack(fill="x", pady=(10, 14)); app.wrap_to_parent(note)
        bar = ttk.Frame(body, style="Card.TFrame"); bar.pack(fill="x")
        view = ttk.Combobox(bar, textvariable=self.view, values=("Clients", "Rules"), state="readonly", width=9)
        view.pack(side="left", padx=(0, 8))
        search = ttk.Entry(bar, textvariable=self.search); search.pack(side="left", fill="x", expand=True, padx=(0, 8))
        status = ttk.Combobox(bar, textvariable=self.filter, values=("All states", "Enabled", "Disabled", "Not specified"), state="readonly", width=16)
        status.pack(side="right")
        ttk.Label(body, text="Search names, workspace names, target IDs or rule paths", style="Muted.TLabel").pack(anchor="w", pady=(5, 10))
        self.summary = tk.StringVar(value="No catalog loaded.")
        label = ttk.Label(body, textvariable=self.summary, style="Muted.TLabel", wraplength=650)
        label.pack(fill="x", pady=(0, 10)); app.wrap_to_parent(label)
        frame = ttk.Frame(body); frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("name", "state", "info", "path"), show="headings", height=9, selectmode="browse")
        for key, title, width in (("name", "Client / rule", 220), ("state", "Configured state", 130), ("info", "Workspace / severity", 160), ("path", "Target / path", 250)):
            self.tree.heading(key, text=title); self.tree.column(key, width=width, minwidth=70)
        vertical = ttk.Scrollbar(frame, command=self.tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew"); vertical.grid(row=0, column=1, sticky="ns"); horizontal.grid(row=1, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1); frame.rowconfigure(0, weight=1)
        ttk.Label(body, text="Assignments and source configuration", style="Section.TLabel").pack(anchor="w", pady=(14, 8))
        detailframe = ttk.Frame(body); detailframe.pack(fill="both", expand=True)
        self.details = tk.Text(detailframe, height=10, wrap="word", state="disabled", font=("Consolas", 10))
        scroll = ttk.Scrollbar(detailframe, command=self.details.yview)
        self.details.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y"); self.details.pack(fill="both", expand=True)
        app.button(body, "Show catalog issues", self.issues).pack(anchor="w", pady=(10, 0))
        app.button(body, "Edit selected rule as draft", self.edit_selected).pack(anchor="w", pady=(8, 0))
        app.button(body, "Preview / deploy selected rule for clients", self.preview_selected).pack(anchor="w", pady=(8, 0))
        self.tree.bind("<<TreeviewSelect>>", self.selected)
        self.mode.trace_add("write", self.mode_changed)
        self.source.trace_add("write", self.invalidate)
        for var in (self.search, self.view, self.filter):
            var.trace_add("write", lambda *args: self.render())

    def edit_selected(self):
        if self.app.busy: return
        selected = self.tree.selection()
        if self.view.get() != "Rules" or not selected or not self.snapshot:
            self.note.set("Choose Rules and select a rule to edit a local draft.")
            return
        row = self.visible[int(selected[0])]
        source = None
        if self.snapshot.get("repository"):
            source = {"repository": self.snapshot["repository"], "revision": self.snapshot["revision"], "path": row["path"]}
        if self.app.rule_builder.edit_rule(row["raw"], source=source):
            self.app.tabs.select(6)

    def preview_selected(self):
        if self.app.busy: return
        selected = self.tree.selection()
        if self.view.get() != "Rules" or not selected or not self.snapshot:
            self.note.set("Choose Rules and select the rule to preview."); return
        if not self.snapshot.get("repository") or not self.snapshot.get("private"):
            self.note.set("Load the private GitHub repository catalog before starting previews."); return
        if self.snapshot["issues"]:
            self.note.set("Resolve the catalog issues before starting previews."); return
        PreviewDialog(self.app, self.snapshot, self.visible[int(selected[0])])

    def mode_changed(self, *args):
        self.source_label.set("Detection repository folder" if self.mode.get() == "Local folder" else "GitHub repository — owner/name")
        self.source.set("")

    def invalidate(self, *args):
        self.snapshot = None
        self.note.set("Source changed. Refresh to load this catalog.")
        self.render()

    def choose(self):
        if self.app.busy: return
        folder = filedialog.askdirectory(parent=self.app.root, title="Choose detection repository root")
        if folder:
            self.mode.set("Local folder"); self.source.set(folder); self.load()

    def detail(self, text):
        self.details.configure(state="normal"); self.details.delete("1.0", "end")
        self.details.insert("1.0", text); self.details.configure(state="disabled")

    def render(self):
        self.tree.delete(*self.tree.get_children()); self.detail(""); self.visible = []
        if self.snapshot is None:
            self.summary.set("No catalog loaded."); return
        needle = self.search.get().strip().casefold()
        kind = self.view.get().lower()
        for row in self.snapshot[kind]:
            values = (row["name"], row["state"], row.get("workspace", row.get("severity")), row.get("target", row["path"]))
            if self.filter.get() not in ("All states", row["state"]): continue
            if needle and needle not in (" ".join(values) + " " + row["path"]).casefold(): continue
            self.tree.insert("", "end", iid=str(len(self.visible)), values=values); self.visible.append(row)
        issues = len(self.snapshot["issues"])
        prefix = "Incomplete catalog — " if issues else ""
        self.summary.set(f"{prefix}{len(self.snapshot['clients'])} workspaces | {len(self.snapshot['rules'])} rules | {issues} issues | {len(self.visible)} shown")

    def selected(self, event=None):
        selected = self.tree.selection()
        if not selected or not self.snapshot: return
        row = self.visible[int(selected[0])]
        lines = ["Repository configuration only — not a live Sentinel result.", "Source: " + row["path"], ""]
        if self.view.get() == "Clients":
            rules = {r["path"]: r for r in self.snapshot["rules"]}
            lines.append("Configured rule assignments:")
            for assignment in row["assignments"]:
                rule = rules.get(assignment["path"])
                override = assignment.get("overrides", {})
                effective = override.get("enabled", rule["raw"].get("enabled") if rule else None)
                lines.append(f"• {rule['name'] if rule else 'Unavailable rule'} | {state(effective)} | {assignment['path']}")
            if not row["assignments"]: lines.append("No rule assignments.")
            lines.append("State reflects the explicit enabled field and its assignment override; unspecified is not disabled.")
        else:
            lines.append("Assigned targets:")
            targets = [c for c in self.snapshot["clients"] if any(a["path"] == row["path"] for a in c["assignments"])]
            lines.extend(c["target"] + " (target " + c["state"].lower() + ")" for c in targets)
            if not targets: lines.append("No assignments found in the loaded catalog.")
        lines.extend(["", "Raw configuration", json.dumps(row["raw"], indent=2, ensure_ascii=False, default=str)])
        self.detail("\n".join(lines))

    def issues(self):
        if self.snapshot:
            self.detail("\n".join(self.snapshot["issues"]) or "No catalog reading issues. Pipeline validation and live workspace validation have not been run.")

    def load(self):
        if self.app.busy: return
        key = (self.mode.get(), self.source.get().strip())
        self.snapshot = None; self.render(); self.note.set("Reading catalog…")
        def task():
            try:
                if not key[1]: raise ValueError("Choose a repository folder or enter owner/name first.")
                snapshot = load_local(key[1]) if key[0] == "Local folder" else GitHubReader().load(key[1])
                return snapshot, None
            except Exception as error:
                return None, str(error)
        def done(result):
            if key != (self.mode.get(), self.source.get().strip()): return
            snapshot, error = result
            if error:
                self.note.set("Catalog unavailable: " + error); self.app.status.set("Catalog read failed. No cloud changes made."); return
            self.snapshot = snapshot; self.render()
            self.note.set(f"{snapshot['identity']} | {snapshot['source']}\n{snapshot['revision']}\nLoaded {snapshot['loaded_at']} — refresh for current data.")
            self.app.status.set("Repository catalog loaded. Read-only snapshot; no deployment performed.")
        self.app.work("Reading repository catalog…", task, done, page=5)
