"""Guided local rule drafts with explicit validation and export."""
import re
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from . import rule_drafts as drafts
from . import rule_schema
from . import ui_kit as kit
from .desktop_theme import COLORS, FONTS, px
from .rule_review_dialog import ReviewDialog
from .repository_catalog import GitHubReader
from .repository_reviews import load_request
from .review_history import ReviewHistoryDialog

STEPS = (("Basics", "Name, severity and owner"), ("Query & schedule", "KQL and how often it runs"),
         ("MITRE ATT&CK", "Tactics and techniques"), ("Advanced", "Entities, details and grouping"),
         ("Review", "The file you will submit"))
LIFECYCLES = (("baseline", "Baseline"), ("production", "Production"), ("retired", "Retired"))
OPERATORS = (("GreaterThan", "is more than"), ("LessThan", "is fewer than"), ("Equal", "equals"), ("NotEqual", "is not"))
UNITS = (("minutes", 60), ("hours", 3600), ("days", 86400))
RECENT_DRAFTS = 10


def note_tone(text):
    """Banner tone for a builder message."""
    if text.startswith("Needs attention"): return "danger"
    if text.startswith(("Unsaved", "Missing MITRE", "Couldn't")): return "warning"
    if text.startswith(("Rule check passed", "Rule file exported", "Draft saved", "Submitted", "Matched")): return "success"
    return "info"


SCHEDULE_ADVICE = ("Run every must be between 5 minutes and 14 days, and Look back at least as long as Run every, "
                   "up to 14 days.")
# (words in the check's message, field to point at, plain advice). First match wins.
ISSUES = (
    ("name must be nonempty", "name", "Give the rule a name."),
    ("description must be nonempty", "description", "Describe what the rule detects and why."),
    ("description must be at least 20", "description", "Use at least 20 characters to say what it detects and why."),
    ("owner must be nonempty", "owner", "Enter the owning team's identifier, for example soc-team."),
    ("owner must be a team", "owner", "Use a lowercase identifier without spaces, for example soc-team."),
    ("query must be nonempty", "query", "Paste the KQL query."),
    ("dangling pipe", "query", "The query ends with |. Remove it or finish the last step."),
    ("Invalid severity", "severity", "Choose a severity."),
    ("Invalid status", "status", "Choose a lifecycle."),
    ("Retired rules must be disabled", "enabled", "A retired rule can't be enabled. Clear this box or change the lifecycle."),
    ("Invalid triggerOperator", "triggerOperator", "Choose when an alert is raised."),
    ("NRT rules must omit", "advanced", "Remove schedule and threshold properties from Additional rule properties."),
    ("hreshold", "triggerThreshold", "Enter a whole number of 0 or more, such as 0."),
    ("Unknown MITRE tactic", "tactics", "One of these isn't a MITRE tactic name. Clear the field and add tactics with Add tactic."),
    ("techniques accepts parent IDs", "techniques",
     "Parent techniques look like T1562. Put IDs with a dot, such as T1562.004, under Sub-techniques."),
    ("subTechniques requires IDs", "subTechniques", "Sub-techniques look like T1562.004."),
    ("Include the parent technique", "techniques", "Add each sub-technique's parent here, for example T1562 for T1562.004."),
    ("Missing MITRE metadata", "tactics", "Add at least one tactic and one parent technique."),
    ("Use the guided fields for", "advanced", None), ("Unknown rule fields", "advanced", None),
    ("has an invalid type", "advanced", None), ("alertRuleTemplateName", "advanced", None),
)


def locate(form, message):
    """The form field a check failure is about, with advice; (None, message) when unknown."""
    if any(words in message for words in ("ISO 8601 duration", "Duration must be positive", "frequency/period")):
        try:
            frequency = rule_schema.seconds(form["queryFrequency"])
        except ValueError:
            return "queryFrequency", SCHEDULE_ADVICE
        if not 300 <= frequency <= 14 * 86400:
            return "queryFrequency", SCHEDULE_ADVICE
        return "queryPeriod", SCHEDULE_ADVICE
    for words, key, advice in ISSUES:
        if words in message:
            return key, advice or message
    return None, message


def split_duration(value):
    """'PT90M' -> (90, 'minutes'); the largest unit that divides evenly. ('', 'hours') when unreadable."""
    try: total = rule_schema.seconds(value)
    except (ValueError, TypeError): return "", "hours"
    for unit, size in reversed(UNITS):
        if total % size == 0: return str(total // size), unit
    return str(total // 60), "minutes"


def join_duration(amount, unit):
    """(2, 'hours') -> 'PT2H'. Non-numeric amounts pass through so the check can explain them."""
    if not str(amount).strip().isdigit(): return str(amount).strip()
    return {"minutes": "PT{}M", "hours": "PT{}H", "days": "P{}D"}[unit].format(int(amount))


def draft_name(name):
    return (re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:80] or "new-rule") + ".rule-draft.json"


class RuleBuilderPage:
    def __init__(self, app, parent, data_dir):
        self.app, self.data_dir = app, Path(data_dir)
        self.loading = True
        self.draft_path = None
        self.source = None
        self.source_uploaded = False  # Source was matched from an uploaded rule file, not a library edit.
        self.submitted_url = None
        self.vars, self.texts = {}, {}
        self.widgets, self.labels, self.flagged = {}, {}, None
        self.github_user, self.github_checking = None, False
        self.saved = None
        enabled = lambda: not app.busy

        toolbar = ttk.Frame(parent); toolbar.pack(fill="x", pady=(0, px(14)))
        self.open_menu = tk.Menu(app.root, tearoff=0, font=FONTS["body"], bg=COLORS["card"], fg=COLORS["ink"], bd=0,
                                 activebackground=COLORS["accent_soft"], activeforeground=COLORS["ink"])
        for title, command, tip in (("New rule", self.new, "Start an empty rule (Ctrl+N)"),
                                    ("Edit existing rule…", self.app.open_rule_library, "Pick a rule from the library to change"),
                                    ("Open…", self.show_open_menu, "Recent drafts, or a rule file from disk"),
                                    ("Save draft", self.save, "Keep this draft on this PC (Ctrl+S)")):
            button = app.button(toolbar, title, command)
            button.pack(side="left", padx=(0, px(8))); kit.tooltip(button, tip)
            if title == "Open…": self.open_button = button
        self.note = tk.StringVar()
        self.banner = kit.Banner(parent, self.note)
        self.banner.pack(fill="x", pady=(0, kit.space("lg")))
        self.note.trace_add("write", lambda *args: self.banner.tone(note_tone(self.note.get())))
        self.github_message = tk.StringVar()
        self.github_bar = kit.Banner(parent, self.github_message, "warning")
        self.github_action = ttk.Button(self.github_bar.actions, style="Small.Secondary.TButton")
        self.github_action.pack(side="right")
        self.github_retry = ttk.Button(self.github_bar.actions, text="Check again", style="Small.Ghost.TButton",
                                       command=lambda: self.check_github(force=True))
        self.github_retry.pack(side="right", padx=(0, px(6)))
        self.github_bar.action_button = self.github_action  # So the banner's wrap leaves room for the buttons.

        layout = self.layout = ttk.Frame(parent); layout.pack(fill="both", expand=True)
        layout.columnconfigure(1, weight=1); layout.rowconfigure(0, weight=1)
        steps_card = kit.Card(layout, padding=10)
        steps_card.frame.grid(row=0, column=0, sticky="nsw", padx=(0, kit.space("lg")))
        form_card = kit.Card(layout, STEPS[0][0], STEPS[0][1])
        form_card.frame.grid(row=0, column=1, sticky="nsew")
        self.form_card = form_card
        self.state_pill = kit.Pill(form_card.actions, "New draft", "info")
        self.state_pill.pack()
        self.steps = ttk.Notebook(form_card.body, style="CardHidden.TNotebook", takefocus=0); self.steps.pack(fill="both", expand=True)
        pages = []
        for name, _ in STEPS:
            page = ttk.Frame(self.steps, style="Card.TFrame")
            self.steps.add(page, text=name); pages.append(page)
        kit.Stepper(steps_card.body, self.steps, STEPS, enabled).pack(fill="x")
        self.steps.bind("<<NotebookTabChanged>>", self.step_changed, add="+")
        basics, query, mitre, advanced, review = self.pages = pages

        self.field(basics, "Rule name", "name")
        self.textbox(basics, "Description", "description", 4, "Explain what this detects and why — at least 20 characters.")
        kit.field_label(basics, "Severity")
        self.choice(basics, "severity", ("Informational", "Low", "Medium", "High"), "Severity")
        self.pair(basics, ("Owner (team identifier)", "owner", None, "For example soc-team"),
                  ("Lifecycle", "status", LIFECYCLES))
        kit.field_label(basics, "Rule type")
        self.choice(basics, "kind", ("Scheduled", "NRT"), "Rule type", labels={"NRT": "Near real-time"})
        flags = ttk.Frame(basics, style="Card.TFrame"); flags.pack(fill="x", pady=(0, px(6)))
        self.check(flags, "Enable the rule when it is deployed", "enabled")
        self.check(flags, "Create incidents from alerts", "createIncident")
        self.field(basics, "Rule ID", "id", readonly=True, hint="Kept when you edit a rule; New rule creates a new one")

        self.textbox(query, "Query (KQL)", "query", 12,
                     "Write the query in KQL. It runs against the client's workspace after approval.", font="mono")
        self.schedule = ttk.Frame(query, style="Card.TFrame"); self.schedule.pack(fill="x")
        row = ttk.Frame(self.schedule, style="Card.TFrame"); row.pack(fill="x")
        row.columnconfigure((0, 1), weight=1, uniform="fields")
        left = ttk.Frame(row, style="Card.TFrame"); left.grid(row=0, column=0, sticky="new", padx=(0, px(12)))
        right = ttk.Frame(row, style="Card.TFrame"); right.grid(row=0, column=1, sticky="new")
        self.duration(left, "Run every", "queryFrequency")
        self.duration(right, "Look back", "queryPeriod", hint="Same as Run every, or longer")
        self.pair(self.schedule, ("Alert when the number of results", "triggerOperator", OPERATORS),
                  ("Threshold", "triggerThreshold", None))
        self.hint(query, "Scheduled rules run every 5 minutes to 14 days. Near real-time rules have no schedule or "
                         "threshold. The query is tested against the workspace after approval.")

        self.field(mitre, "Tactics", "tactics", hint="Comma-separated; add them with the picker below")
        picker = ttk.Frame(mitre, style="Card.TFrame"); picker.pack(fill="x", pady=(0, kit.space("lg")))
        self.tactic_choice = tk.StringVar(value="DefenseEvasion")
        choices = ttk.Combobox(picker, textvariable=self.tactic_choice,
                               values=sorted(rule_schema.TACTICS), state="readonly")
        choices.pack(side="left", fill="x", expand=True, padx=(0, px(8)))
        app.controls.append((choices, "readonly"))
        app.button(picker, "Add tactic", self.add_tactic).pack(side="left")
        self.field(mitre, "Parent techniques", "techniques", hint="For example T1562, T1003")
        self.field(mitre, "Sub-techniques", "subTechniques", hint="For example T1562.004 — include its parent above")
        self.check(mitre, "Allow missing MITRE details for a migrated rule", "allow_missing_mitre")
        self.hint(mitre, "This only relaxes this check. Each client the rule is assigned to must also allow it.")

        self.hint(advanced, "Optional properties such as entityMappings, customDetails, suppression and grouping, "
                            "in the rule file's format. Existing properties are kept. Leave {} for a simple rule.")
        self.textbox(advanced, "Additional rule properties", "advanced", 16, font="mono")

        self.hint(review, "Select Check rule to build the file that will be submitted. The check covers the rule's "
                          "format, not the query results or deployment permissions.")
        self.preview = self.make_text(review, 19, readonly=True)

        self.footer = kit.Card(app.footer_slot, padding=12)
        actions = self.footer.body
        app.button(actions, "My submissions…", self.review_history, "Ghost.TButton").pack(side="left")
        self.github_label = ttk.Label(actions, style="Hint.TLabel")
        self.github_label.pack(side="left", padx=(px(8), 0))
        app.button(actions, "Submit for approval", self.review, "Primary.TButton").pack(side="right")
        app.button(actions, "Check rule", self.validate).pack(side="right", padx=(0, px(8)))
        app.button(actions, "Export rule file…", self.export).pack(side="right", padx=(0, px(8)))
        self.vars["kind"].trace_add("write", self.kind_changed)
        self.load_form(drafts.new_form())

    # ------------------------------------------------------------ widgets
    def set_footer_visible(self, visible):
        if visible and not self.footer.frame.winfo_manager():
            self.footer.frame.pack(fill="x", padx=kit.space("page"), pady=(0, kit.space("md")))
        elif not visible and self.footer.frame.winfo_manager():
            self.footer.frame.pack_forget()

    def step_changed(self, event=None):
        title, description = STEPS[self.steps.index("current")]
        self.form_card.title_var.set(title); self.form_card.subtitle_var.set(description)

    def add_tactic(self):
        if self.app.busy: return
        current = [value.strip() for value in self.vars["tactics"].get().split(",") if value.strip()]
        if self.tactic_choice.get() not in current:
            current.append(self.tactic_choice.get())
            self.vars["tactics"].set(", ".join(current))

    def hint(self, parent, text):
        label = ttk.Label(parent, text=text, style="Hint.TLabel", wraplength=px(600), justify="left")
        label.pack(fill="x", pady=(0, px(14))); kit.wrap_to_parent(label)

    def field(self, parent, label, key, choices=None, hint=None, readonly=False):
        """An entry, or a drop-down whose ``choices`` are (value, shown text) pairs."""
        frame = ttk.Frame(parent, style="Card.TFrame"); frame.pack(fill="x", pady=(0, px(14)))
        kit.field_label(frame, label, hint)
        var = tk.StringVar(); self.vars[key] = var
        if choices:
            pairs = [(c, c) if isinstance(c, str) else tuple(c) for c in choices]
            shown = tk.StringVar()
            control = ttk.Combobox(frame, textvariable=shown, values=[text for _, text in pairs], state="readonly")
            def to_value(*args):
                for value, text in pairs:
                    if text == shown.get() and var.get() != value: var.set(value)
            def to_text(*args):
                text = next((text for value, text in pairs if value == var.get()), var.get())
                if shown.get() != text: shown.set(text)
            shown.trace_add("write", to_value); var.trace_add("write", to_text)
        else:
            control = ttk.Entry(frame, textvariable=var, state="readonly" if readonly else "normal")
        control.pack(fill="x"); self.app.controls.append((control, "readonly" if choices or readonly else "normal"))
        self.widgets[key], self.labels[key] = control, label
        var.trace_add("write", self.changed)

    def duration(self, parent, label, key, hint=None):
        """A number plus a unit, stored as an ISO 8601 duration in vars[key]."""
        frame = ttk.Frame(parent, style="Card.TFrame"); frame.pack(fill="x", pady=(0, px(14)))
        kit.field_label(frame, label, hint)
        var = tk.StringVar(); self.vars[key] = var
        amount, unit = tk.StringVar(), tk.StringVar(value="hours")
        row = ttk.Frame(frame, style="Card.TFrame"); row.pack(fill="x")
        spin = ttk.Spinbox(row, textvariable=amount, from_=1, to=20160, width=6)
        spin.pack(side="left", fill="x", expand=True)
        units = ttk.Combobox(row, textvariable=unit, values=[name for name, _ in UNITS], state="readonly", width=9)
        units.pack(side="left", padx=(px(8), 0))
        self.app.controls.append((spin, "normal")); self.app.controls.append((units, "readonly"))
        guard = {"busy": False}
        def compose(*args):
            if guard["busy"]: return
            guard["busy"] = True
            try: var.set(join_duration(amount.get(), unit.get()))
            finally: guard["busy"] = False
        def decompose(*args):
            if guard["busy"]: return
            guard["busy"] = True
            try:
                number, name = split_duration(var.get())
                if amount.get() != number: amount.set(number if number else var.get())
                if unit.get() != name: unit.set(name)
            finally: guard["busy"] = False
        amount.trace_add("write", compose); unit.trace_add("write", compose)
        var.trace_add("write", decompose)
        self.widgets[key], self.labels[key] = spin, label
        var.trace_add("write", self.changed)

    def choice(self, parent, key, options, label, labels=None):
        var = tk.StringVar(); self.vars[key] = var
        control = kit.Segmented(parent, var, [(value, (labels or {}).get(value, value)) for value in options],
                                enabled=lambda: not self.app.busy)
        control.pack(anchor="w", pady=(0, px(14)))
        self.widgets[key], self.labels[key] = control, label
        var.trace_add("write", self.changed)

    def pair(self, parent, left, right):
        row = ttk.Frame(parent, style="Card.TFrame"); row.pack(fill="x")
        for index, spec in enumerate((left, right)):
            row.columnconfigure(index, weight=1, uniform="fields")
            col = ttk.Frame(row, style="Card.TFrame"); col.grid(row=0, column=index, sticky="new", padx=(0, px(12)) if not index else 0)
            self.field(col, *spec)

    def check(self, parent, label, key):
        var = tk.BooleanVar(); self.vars[key] = var
        control = ttk.Checkbutton(parent, text=label, variable=var)
        control.pack(anchor="w", pady=(0, px(6))); self.app.controls.append((control, "normal"))
        self.widgets[key], self.labels[key] = control, label
        var.trace_add("write", self.changed)

    def make_text(self, parent, height, readonly=False, font="mono"):
        frame, text = kit.text_area(parent, height, readonly=readonly, undo=not readonly, font=font)
        frame.pack(fill="both", expand=True, pady=(0, px(14)))
        if not readonly: self.app.controls.append((text, "normal"))
        return text

    def textbox(self, parent, label, key, height, hint=None, font="body"):
        kit.field_label(parent, label)
        if hint: self.hint(parent, hint)
        text = self.make_text(parent, height, font=font); self.texts[key] = text
        self.widgets[key], self.labels[key] = text, label
        text.bind("<<Modified>>", lambda event: self.text_changed(text))

    def text_changed(self, text):
        if text.edit_modified():
            text.edit_modified(False); self.changed()

    # ------------------------------------------------------------ state
    def form(self):
        return {**{k: v.get() for k, v in self.vars.items()},
                **{k: v.get("1.0", "end-1c") for k, v in self.texts.items()}}

    def dirty(self):
        return self.saved is not None and self.form() != self.saved

    def changed(self, *args):
        if self.loading: return
        self.clear_flag()
        self.submitted_url = None
        self.set_preview("")
        self.note.set("Unsaved changes — select Check rule again before submitting." if self.dirty()
                      else "Local draft. Select Check rule when it's ready, then Submit for approval.")

    def kind_changed(self, *args):
        if self.vars["kind"].get() == "NRT": self.schedule.pack_forget()
        elif not self.schedule.winfo_manager(): self.schedule.pack(fill="x", after=self.texts["query"].master)

    def render_state(self):
        if self.submitted_url: self.state_pill.set("Submitted", "success")
        elif self.preview.get("1.0", "end-1c"): self.state_pill.set("Checked", "success")
        elif self.dirty(): self.state_pill.set("Unsaved changes", "warning")
        elif self.draft_path: self.state_pill.set("Saved draft", "neutral")
        else: self.state_pill.set("New draft", "info")

    def set_preview(self, text):
        kit.set_text(self.preview, text)
        self.render_state()

    def load_form(self, form, path=None, source=None):
        self.loading = True
        for key, var in self.vars.items(): var.set(form[key])
        for key, text in self.texts.items():
            text.delete("1.0", "end"); text.insert("1.0", form[key]); text.edit_modified(False); text.edit_reset()
        self.draft_path = path; self.source = source; self.source_uploaded = False; self.submitted_url = None
        self.saved = self.form(); self.loading = False
        self.changed(); self.steps.select(0)

    def can_discard(self):
        return not self.dirty() or messagebox.askyesno("Discard changes?",
            "This rule has unsaved changes. Discard them? Select No, then Save draft, to keep them.", parent=self.app.root)

    def new(self):
        if not self.app.busy and self.can_discard(): self.load_form(drafts.new_form())

    def error(self, error):
        self.set_preview(""); self.note.set("Needs attention: " + str(error))

    # ------------------------------------------------------------ files
    def recent_drafts(self):
        folder = self.data_dir / "drafts"
        if not folder.exists(): return []
        files = sorted((p for p in folder.glob("*.rule-draft.json") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[:RECENT_DRAFTS]

    def show_open_menu(self):
        if self.app.busy: return
        menu = self.open_menu
        menu.delete(0, "end")
        menu.add_command(label="Open rule file…", command=self.open_yaml)
        menu.add_command(label="Open draft file…", command=self.open_draft)
        recent = self.recent_drafts()
        if recent:
            menu.add_separator()
            for path in recent:
                menu.add_command(label=path.name.replace(".rule-draft.json", ""), command=lambda p=path: self.open_path(p, True))
        try: menu.tk_popup(self.open_button.winfo_rootx(), self.open_button.winfo_rooty() + self.open_button.winfo_height())
        finally: menu.grab_release()

    def open_yaml(self):
        self.open_file(False)

    def open_draft(self):
        self.open_file(True)

    def open_file(self, draft):
        if self.app.busy: return
        path = filedialog.askopenfilename(parent=self.app.root, title="Open draft" if draft else "Open rule file",
            initialdir=str(self.data_dir / "drafts") if draft and (self.data_dir / "drafts").exists() else None,
            filetypes=[("Rule draft", "*.rule-draft.json")] if draft else [("Rule file", "*.yml *.yaml")])
        if path: self.open_path(path, draft)

    def open_path(self, path, draft):
        try:
            form = drafts.read_draft(path) if draft else drafts.read_rule(path)
            source = drafts.read_draft_source(path) if draft else None
            if not self.can_discard(): return
            self.load_form(form, str(path) if draft else None, source)
        except (ValueError, OSError, RecursionError) as error: self.error(error); return
        if not draft: self.match_library(form)

    def match_library(self, form):
        """Link an opened rule file to the library rule with the same ID, so it can be submitted as an update."""
        repository = self.app.github_repository()
        if "/" not in repository:
            self.note.set("Opened as a new rule. To update a rule that is already in the library, set the rule library "
                          "on Connect or use Edit existing rule.")
            return
        def task():
            try: return GitHubReader().load(repository, self.app.file_progress(self.note.set)), None
            except Exception as error: return None, str(error)
        def done(result):
            snapshot, error = result
            if self.vars["id"].get() != form["id"]: return  # Another rule was opened meanwhile.
            if error:
                self.app.log("Library check: " + error)
                self.note.set("Couldn't check the library, so this opened as a new rule. If it is already in the "
                              "library, use Edit existing rule instead.")
                return
            match = next((rule for rule in snapshot["rules"] if str(rule.get("id", "")).lower() == form["id"].lower()), None)
            if match is None:
                self.note.set("Opened as a new rule — no rule with this ID is in the library yet.")
                return
            self.source = {"repository": repository, "revision": snapshot["revision"], "path": match["path"]}
            self.source_uploaded = True
            self.note.set("Matched “" + match["name"] + "” in the library (" + match["path"] + "). Submitting will "
                          "update that rule; you will see exactly what changes before anything is sent.")
        self.app.work("Checking whether this rule is already in the rule library…", task, done, page="builder")

    def edit_rule(self, rule, source=None):
        if self.app.busy: return False
        try:
            form = drafts.from_rule(rule)
            if not self.can_discard(): return False
            self.load_form(form, source=source)
            self.note.set("Library rule opened as a draft. Its rule ID and advanced properties are kept.")
            return True
        except (ValueError, OSError, RecursionError) as error: self.error(error); return False

    def save(self):
        if self.app.busy: return
        try:
            folder = self.data_dir / "drafts"; folder.mkdir(parents=True, exist_ok=True)
            path = Path(self.draft_path) if self.draft_path else folder / draft_name(self.vars["name"].get())
            if not self.draft_path and path.exists():
                try: owner = drafts.read_draft(path)["id"]
                except (ValueError, OSError): owner = None
                if owner and owner != self.vars["id"].get():  # A different rule already uses this name.
                    chosen = filedialog.asksaveasfilename(parent=self.app.root, initialdir=folder, initialfile=path.name,
                        title="Save draft as", defaultextension=".rule-draft.json", filetypes=[("Rule draft", "*.rule-draft.json")])
                    if not chosen: return
                    path = Path(chosen)
            drafts.save_draft(path, self.form(), self.source); self.draft_path = str(path); self.saved = self.form()
            self.note.set("Draft saved on this PC as " + path.name.replace(".rule-draft.json", "") + ". Find it under Open….")
            self.render_state()
        except (ValueError, OSError) as error: self.error(error)

    # ------------------------------------------------------------ checks
    def validate(self):
        if self.app.busy: return None
        try:
            text, warnings = drafts.validated_yaml(self.form())
            self.set_preview(text); self.steps.select(4)
            self.note.set("Rule check passed. " + ("; ".join(warnings) + ". Each assigned client must allow this. " if warnings else "")
                          + "Select Submit for approval when you're ready. The query is tested against the workspace after approval.")
            return text, warnings
        except (ValueError, OSError, TypeError, RecursionError) as error: self.problem(error); return None

    def step_of(self, key):
        widget = self.widgets[key]
        while widget is not None and widget not in self.pages: widget = widget.master
        return self.pages.index(widget) if widget is not None else 0

    def problem(self, error):
        """Show a failed check next to the field it is about, instead of a dialog."""
        key, advice = locate(self.form(), str(error))
        self.set_preview("")
        if key is None:
            self.note.set("Needs attention: " + advice); return
        step = self.step_of(key)
        self.steps.select(step); self.flag(key, advice)
        self.note.set("Needs attention — " + self.labels[key] + " (step " + str(step + 1) + ", " + STEPS[step][0] + "): " + advice)

    def flag(self, key, advice=""):
        self.clear_flag()
        widget = self.widgets[key]
        # Repeat the advice under the field: the banner may be scrolled out of view.
        anchor = widget.master if isinstance(widget, (tk.Text, ttk.Spinbox)) else widget
        # The wrap width follows the field, not the label's own size, which would feed back into the grid.
        self.flag_label = tk.Label(anchor.master, text=advice, font=FONTS["small"], fg=COLORS["danger"],
                                   bg=COLORS["card"], justify="left", anchor="w", bd=0,
                                   wraplength=max(px(120), anchor.winfo_width()))
        self.flag_label.pack(fill="x", after=anchor, pady=(px(4), px(8)))
        self.flag_binding = anchor.bind("<Configure>", lambda event: self.flag_label.winfo_exists() and
                                        self.flag_label.configure(wraplength=max(px(120), event.width)), add="+")
        self.flag_anchor = anchor
        if isinstance(widget, (ttk.Entry, ttk.Combobox, ttk.Spinbox)):
            widget.configure(style="Error.T" + widget.winfo_class()[1:])
        elif isinstance(widget, tk.Text):
            widget.master.error = True
            widget.master.configure(highlightbackground=COLORS["danger"], highlightthickness=2)
        elif isinstance(widget, kit.Segmented):
            widget.configure(highlightbackground=COLORS["danger"], highlightthickness=2)
        self.flagged = key
        self.app.scroll_into_view(widget)
        try: widget.focus_set()
        except tk.TclError: pass

    def clear_flag(self):
        if self.flagged is None: return
        widget = self.widgets[self.flagged]; self.flagged = None
        self.flag_label.destroy()
        try: self.flag_anchor.unbind("<Configure>", self.flag_binding)
        except (tk.TclError, AttributeError): pass
        if isinstance(widget, (ttk.Entry, ttk.Combobox, ttk.Spinbox)):
            widget.configure(style=widget.winfo_class())
        elif isinstance(widget, tk.Text):
            widget.master.error = False
            widget.master.configure(highlightbackground=COLORS["line"], highlightthickness=1)
        elif isinstance(widget, kit.Segmented):
            widget.configure(highlightbackground=COLORS["line"], highlightthickness=1)

    # ------------------------------------------------------------ GitHub sign-in
    @staticmethod
    def github_probe():
        return GitHubReader.get("user")["login"]

    def show_github_bar(self, message, action, command):
        self.github_message.set(message)
        self.github_action.configure(text=action, command=command)
        if not self.github_bar.winfo_manager(): self.github_bar.pack(fill="x", pady=(0, kit.space("lg")), before=self.layout)
        self.github_bar.rewrap()

    def check_github(self, force=False):
        """Make sure submitting will work before the analyst reaches the last step."""
        if self.github_checking or (self.github_user and not force): return
        repository = self.app.github_repository()
        if "/" not in repository:
            self.github_user = None; self.github_label.configure(text="")
            self.app.github_pill.set("Not set")
            self.show_github_bar("Set your team's rule library on Connect before submitting rules. You can still write and check rules.",
                                 "Go to Connect", self.app.show_github_settings)
            return
        self.github_checking = True
        self.github_label.configure(text="Checking GitHub sign-in…")
        self.app.github_pill.set("Checking…", "info")
        probe = self.github_probe
        def run():
            try: result = (probe(), None)
            except Exception as error: result = (None, str(error))
            self.app.call_soon(lambda: self.github_checked(*result))
        threading.Thread(target=run, daemon=True).start()

    def github_checked(self, user, error):
        self.github_checking = False
        self.github_user = user
        error = error or ""
        missing = "not installed" in error
        offline = any(words in error.casefold() for words in ("timed out", "could not resolve", "connection", "network"))
        if user:
            self.github_bar.pack_forget()
            self.github_label.configure(text="Signed in to GitHub as " + user)
            self.app.github_status.set("Signed in to GitHub as " + user + ".")
            self.app.github_pill.set("Signed in", "success")
            return
        self.github_label.configure(text="")
        if missing:
            self.app.github_status.set("GitHub CLI isn't installed on this PC.")
            self.app.github_pill.set("GitHub CLI missing", "danger")
            self.show_github_bar("GitHub CLI isn't installed on this PC, so rules can't be submitted yet. "
                                 "Ask your administrator to install it, then select Check again.",
                                 "Help & prerequisites", lambda: self.app.open_help())
        elif offline:
            self.app.github_status.set("Couldn't reach GitHub. Check your network connection.")
            self.app.github_pill.set("Can't reach GitHub", "warning")
            self.show_github_bar("Couldn't reach GitHub. Check your network connection, then select Check again.",
                                 "Check again", lambda: self.check_github(force=True))
        else:
            self.app.github_status.set("Not signed in to GitHub. Select Sign in to GitHub.")
            self.app.github_pill.set("Not signed in", "warning")
            self.show_github_bar("You're not signed in to GitHub, so rules can't be submitted yet. You can still "
                                 "write and check rules.", "Sign in to GitHub", self.app.github_login)

    # ------------------------------------------------------------ actions
    def export(self):
        if self.app.busy or self.validate() is None: return
        path = filedialog.asksaveasfilename(parent=self.app.root, title="Export rule file",
            initialfile=draft_name(self.vars["name"].get()).replace(".rule-draft.json", ".yml"),
            defaultextension=".yml", filetypes=[("Rule file", "*.yml *.yaml")])
        if not path: return
        try:
            warnings = drafts.export_rule(path, self.form())
            self.note.set("Rule file exported to " + path + ". Nothing is submitted or deployed."
                          + (" Missing MITRE details: each assigned client must allow this." if warnings else ""))
        except (ValueError, OSError, TypeError, RecursionError) as error: self.error(error)

    def review(self):
        if not self.app.busy and self.validate() is not None:
            ReviewDialog(self, auto=True)

    def submitted(self, url):
        """Called by the review dialog once a submission exists."""
        self.submitted_url = url
        self.note.set("Submitted for approval. A teammate reviews it in GitHub before anything is deployed.")
        self.banner.set_action("Open in GitHub", lambda: __import__("webbrowser").open(url))
        self.render_state()

    def review_history(self):
        if not self.app.busy:
            ReviewHistoryDialog(self)

    def recover_review(self):
        if self.app.busy: return
        folder = self.data_dir / "review-requests"
        path = filedialog.askopenfilename(parent=self.app.root, title="Open a saved submission",
            initialdir=folder if folder.exists() else self.data_dir,
            filetypes=[("Submission", "*.review.json")])
        if not path: return
        try:
            ReviewDialog(self, load_request(path))
        except (ValueError, OSError, KeyError, TypeError) as error:
            self.error(error)
