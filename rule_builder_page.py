"""Guided local rule drafts with explicit validation and export."""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

import rule_drafts as drafts
import rule_schema
from rule_review_dialog import ReviewDialog
from repository_reviews import load_request


class RuleBuilderPage:
    def __init__(self, app, parent, data_dir):
        self.app, self.data_dir = app, Path(data_dir)
        self.loading = True
        self.draft_path = None
        self.source = None
        self.vars, self.texts = {}, {}
        self.saved = None
        outer, body = app.card(parent, "Create a detection rule",
            "Build a local draft, check its format, then export YAML for repository review. New rules start disabled.")
        outer.pack(fill="both", expand=True, pady=(0, 16))
        bar = ttk.Frame(body, style="Card.TFrame"); bar.pack(fill="x")
        for title, command in (("New draft", self.new), ("Open YAML", self.open_yaml),
                               ("Open draft", self.open_draft), ("Save draft", self.save)):
            app.button(bar, title, command).pack(side="left", padx=(0, 6))
        self.note = tk.StringVar()
        label = ttk.Label(body, textvariable=self.note, style="Muted.TLabel", wraplength=600)
        label.pack(fill="x", pady=12); app.wrap_to_parent(label)
        self.steps = ttk.Notebook(body); self.steps.pack(fill="both", expand=True)
        pages = []
        for name in ("1 Basics", "2 Query & schedule", "3 MITRE", "4 Advanced", "5 Review YAML"):
            page = ttk.Frame(self.steps, padding=12, style="Card.TFrame")
            self.steps.add(page, text=name); pages.append(page)
        basics, query, mitre, advanced, review = pages
        self.field(basics, "Rule name", "name")
        self.textbox(basics, "Description — explain what this detects and why (at least 20 characters)", "description", 4)
        self.pair(basics, ("Owner / team identifier", "owner", None),
                  ("Severity", "severity", ("Informational", "Low", "Medium", "High")))
        self.pair(basics, ("Lifecycle", "status", ("baseline", "production", "retired")),
                  ("Rule type", "kind", ("Scheduled", "NRT")))
        flags = ttk.Frame(basics, style="Card.TFrame"); flags.pack(fill="x")
        self.check(flags, "Enable rule when deployed", "enabled")
        self.check(flags, "Create incidents from alerts", "createIncident")
        self.field(basics, "Rule ID — retained when editing; New draft creates a new ID", "id", readonly=True)
        self.textbox(query, "KQL query — use tables available in the intended workspaces", "query", 12)
        self.schedule = ttk.Frame(query, style="Card.TFrame"); self.schedule.pack(fill="x")
        self.pair(self.schedule, ("Run every (PT5M, PT1H, P1D)", "queryFrequency", None),
                  ("Look back (at least the run interval)", "queryPeriod", None))
        self.pair(self.schedule, ("Alert when result count is", "triggerOperator", ("GreaterThan", "LessThan", "Equal", "NotEqual")),
                  ("Threshold", "triggerThreshold", None))
        self.hint(query, "Scheduled: 5 minutes to 14 days. NRT omits schedule and threshold fields. KQL execution is checked later against the actual workspace.")
        self.field(mitre, "Tactics — comma-separated names", "tactics")
        picker = ttk.Frame(mitre, style="Card.TFrame"); picker.pack(fill="x", pady=(0, 12))
        self.tactic_choice = tk.StringVar(value="DefenseEvasion")
        choices = ttk.Combobox(picker, textvariable=self.tactic_choice,
                              values=sorted(rule_schema.TACTICS), state="readonly")
        choices.pack(side="left", fill="x", expand=True, padx=(0, 8))
        app.controls.append((choices, "readonly"))
        app.button(picker, "Add tactic", self.add_tactic).pack(side="left")

        self.field(mitre, "Parent techniques — e.g. T1562, T1003", "techniques")
        self.field(mitre, "Sub-techniques — e.g. T1562.004 (include its parent above)", "subTechniques")
        self.check(mitre, "Allow missing MITRE metadata for migration", "allow_missing_mitre")
        self.hint(mitre, "This only relaxes this local check. Every destination target must separately permit the migration exception in its workspace manifest.")
        self.hint(advanced, "Optional YAML properties such as entityMappings, customDetails, suppression and grouping. Existing properties are retained. Leave {} for a simple new rule.")
        self.textbox(advanced, "Additional rule properties", "advanced", 16)
        self.hint(review, "Validate to generate the current YAML. This checks the uploaded repository schema, not live table availability, query results, or deployment permissions.")
        self.preview = self.make_text(review, 19, readonly=True)
        actions = ttk.Frame(body, style="Card.TFrame"); actions.pack(fill="x", pady=(14, 0))
        app.button(actions, "Validate & review", self.validate, "Primary.TButton").pack(side="left")
        app.button(actions, "Export YAML", self.export).pack(side="left", padx=8)
        app.button(actions, "Review for GitHub", self.review).pack(side="left")
        app.button(body, "Recover / open a saved review request", self.recover_review).pack(anchor="w", pady=(10, 0))
        self.vars["kind"].trace_add("write", self.kind_changed)
        self.load_form(drafts.new_form())

    def add_tactic(self):
        if self.app.busy: return
        current = [value.strip() for value in self.vars["tactics"].get().split(",") if value.strip()]
        if self.tactic_choice.get() not in current:
            current.append(self.tactic_choice.get())
            self.vars["tactics"].set(", ".join(current))

    def hint(self, parent, text):
        label = ttk.Label(parent, text=text, style="Muted.TLabel", wraplength=600)
        label.pack(fill="x", pady=(0, 12)); self.app.wrap_to_parent(label)

    def field(self, parent, label, key, choices=None, readonly=False):
        frame = ttk.Frame(parent, style="Card.TFrame"); frame.pack(fill="x", pady=(0, 10))
        ttk.Label(frame, text=label, style="Field.TLabel").pack(anchor="w", pady=(0, 4))
        var = tk.StringVar(); self.vars[key] = var
        control = (ttk.Combobox(frame, textvariable=var, values=choices, state="readonly")
                   if choices else ttk.Entry(frame, textvariable=var, state="readonly" if readonly else "normal"))
        control.pack(fill="x"); self.app.controls.append((control, "readonly" if choices or readonly else "normal"))
        var.trace_add("write", self.changed)

    def pair(self, parent, left, right):
        row = ttk.Frame(parent, style="Card.TFrame"); row.pack(fill="x")
        for index, spec in enumerate((left, right)):
            row.columnconfigure(index, weight=1, uniform="fields")
            col = ttk.Frame(row, style="Card.TFrame"); col.grid(row=0, column=index, sticky="ew", padx=(0, 10) if not index else 0)
            self.field(col, *spec)

    def check(self, parent, label, key):
        var = tk.BooleanVar(); self.vars[key] = var
        control = ttk.Checkbutton(parent, text=label, variable=var)
        control.pack(anchor="w", pady=(0, 8)); self.app.controls.append((control, "normal"))
        var.trace_add("write", self.changed)

    def make_text(self, parent, height, readonly=False):
        frame = ttk.Frame(parent); frame.pack(fill="both", expand=True, pady=(0, 10))
        text = tk.Text(frame, height=height, width=30, wrap="word", font=("Consolas", 10), undo=not readonly)
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y"); text.pack(fill="both", expand=True)
        if readonly: text.configure(state="disabled")
        else: self.app.controls.append((text, "normal"))
        return text

    def textbox(self, parent, label, key, height):
        ttk.Label(parent, text=label, style="Field.TLabel").pack(anchor="w", pady=(0, 5))
        text = self.make_text(parent, height); self.texts[key] = text
        text.bind("<<Modified>>", lambda event: self.text_changed(text))

    def text_changed(self, text):
        if text.edit_modified():
            text.edit_modified(False); self.changed()

    def form(self):
        return {**{k: v.get() for k, v in self.vars.items()},
                **{k: v.get("1.0", "end-1c") for k, v in self.texts.items()}}

    def dirty(self):
        return self.saved is not None and self.form() != self.saved

    def changed(self, *args):
        if self.loading: return
        self.set_preview("")
        self.note.set("Unsaved changes — validate again before export." if self.dirty()
                      else "Local draft. Validate before exporting.")

    def kind_changed(self, *args):
        if self.vars["kind"].get() == "NRT": self.schedule.pack_forget()
        elif not self.schedule.winfo_manager(): self.schedule.pack(fill="x")

    def set_preview(self, text):
        self.preview.configure(state="normal"); self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text); self.preview.configure(state="disabled")

    def load_form(self, form, path=None, source=None):
        self.loading = True
        for key, var in self.vars.items(): var.set(form[key])
        for key, text in self.texts.items():
            text.delete("1.0", "end"); text.insert("1.0", form[key]); text.edit_modified(False); text.edit_reset()
        self.draft_path = path; self.source = source; self.saved = self.form(); self.loading = False
        self.changed(); self.steps.select(0)

    def can_discard(self):
        return not self.dirty() or messagebox.askyesno("Unsaved rule draft",
            "Discard the unsaved changes to this rule draft? Save draft first if you want to keep them.", parent=self.app.root)

    def new(self):
        if not self.app.busy and self.can_discard(): self.load_form(drafts.new_form())

    def error(self, error):
        self.set_preview(""); self.note.set("Needs attention: " + str(error))
        messagebox.showerror("Rule draft", str(error), parent=self.app.root)

    def open_yaml(self):
        self.open_file(False)

    def open_draft(self):
        self.open_file(True)

    def open_file(self, draft):
        if self.app.busy: return
        path = filedialog.askopenfilename(parent=self.app.root, title="Open local rule draft" if draft else "Open rule YAML",
            filetypes=[("Rule draft", "*.rule-draft.json")] if draft else [("YAML", "*.yml *.yaml")])
        if not path: return
        try:
            form = drafts.read_draft(path) if draft else drafts.read_rule(path)
            source = drafts.read_draft_source(path) if draft else None
            if self.can_discard(): self.load_form(form, path if draft else None, source)
        except (ValueError, OSError, RecursionError) as error: self.error(error)

    def edit_rule(self, rule, source=None):
        if self.app.busy: return False
        try:
            form = drafts.from_rule(rule)
            if not self.can_discard(): return False
            self.load_form(form, source=source)
            self.note.set("Repository rule copied into a local draft. Its rule ID and advanced properties are retained.")
            return True
        except (ValueError, OSError, RecursionError) as error: self.error(error); return False

    def save(self):
        if self.app.busy: return
        try:
            folder = self.data_dir / "drafts"; folder.mkdir(parents=True, exist_ok=True)
            path = self.draft_path or filedialog.asksaveasfilename(parent=self.app.root, initialdir=folder,
                initialfile="new-rule.rule-draft.json", defaultextension=".rule-draft.json", filetypes=[("Rule draft", "*.rule-draft.json")])
            if not path: return
            drafts.save_draft(path, self.form(), self.source); self.draft_path = path; self.saved = self.form()
            self.note.set("Draft saved locally: " + str(path) + ". It may still need validation.")
        except (ValueError, OSError) as error: self.error(error)

    def validate(self):
        if self.app.busy: return None
        try:
            text, warnings = drafts.validated_yaml(self.form())
            self.set_preview(text); self.steps.select(4)
            self.note.set("Schema check passed. " + ("; ".join(warnings) + ". Destination targets must allow this exception. " if warnings else "")
                          + "Live KQL and deployment checks still run in the repository pipeline.")
            return text, warnings
        except (ValueError, OSError, TypeError, RecursionError) as error: self.error(error); return None

    def export(self):
        if self.app.busy or self.validate() is None: return
        path = filedialog.asksaveasfilename(parent=self.app.root, title="Export rule for repository review",
            initialfile="new-rule.yml", defaultextension=".yml", filetypes=[("YAML", "*.yml *.yaml")])
        if not path: return
        try:
            warnings = drafts.export_rule(path, self.form())
            self.note.set("YAML exported: " + path + ". No clients assigned or deployed. "
                          + ("Missing MITRE requires a target migration exception." if warnings else "Submit through repository review."))
        except (ValueError, OSError, TypeError, RecursionError) as error: self.error(error)

    def review(self):
        if not self.app.busy and self.validate() is not None:
            ReviewDialog(self)

    def recover_review(self):
        if self.app.busy: return
        folder = self.data_dir / "review-requests"
        path = filedialog.askopenfilename(parent=self.app.root, title="Open saved review request",
            initialdir=folder if folder.exists() else self.data_dir,
            filetypes=[("Review request", "*.review.json")])
        if not path: return
        try:
            ReviewDialog(self, load_request(path))
        except (ValueError, OSError, KeyError, TypeError) as error:
            self.error(error)
