"""Guided local rule drafts with explicit validation and export."""
import json
import re
import threading
import tkinter as tk
from datetime import datetime, timezone
from tkinter import ttk, filedialog
from pathlib import Path

from . import advice
from . import kql_check
from . import query_runner
from . import rule_drafts as drafts
from . import rule_schema
from . import ui_kit as kit
from .auth_recovery import AuthenticationRequired
from .desktop_theme import COLORS, FONTS, KQL_THEMES, TONES, px
from .gov_onboarding import GOV_CLOUD
from .kql_editor import KqlEditor, fix_label
from .lighthouse_onboarding import Stop
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
AUTOSAVE_MS = 30000  # How often unsaved work is copied to the recovery draft.
# In drafts/. Not a *.rule-draft.json name, so Open draft file… never lists it as a draft.
RECOVERY_NAME = ".autosave.rule-recovery.json"
TACTIC_COLUMNS = 3
TEST_ROWS = (8, 16)  # Test results show this many rows at least and at most; more scroll.
QUERY_LINES = 7  # One line fewer than it could be: the KQL check line sits under the box.
KQL_DELAY = 300  # Milliseconds after the last keystroke before the query is checked again.
MAX_SCHEDULE = 14 * 86400
# The patterns rule_schema.validate checks inline. That module is a reviewed copy pinned by the hash in
# docs/RULE-SCHEMA.md, so they are repeated here; test_quick_checks_agree_with_the_full_check catches drift.
OWNER = re.compile(r"[a-z0-9][a-z0-9._@-]*")
TECHNIQUE = re.compile(r"T\d{4}")
SUB_TECHNIQUE = re.compile(r"T\d{4}\.\d{3}")


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
    ("Unknown MITRE tactic", "tactics", "Only MITRE tactic names can be used. Select Remove beside the values that "
                                        "aren't, then tick the tactics that apply."),
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
        if not 300 <= frequency <= MAX_SCHEDULE:
            return "queryFrequency", SCHEDULE_ADVICE
        return "queryPeriod", SCHEDULE_ADVICE
    for words, key, advice in ISSUES:
        if words in message:
            return key, advice or message
    return None, message


def entries(value):
    """A comma- or line-separated list field, split the way drafts.to_rule reads it."""
    return [x.strip() for x in str(value).replace("\n", ",").split(",") if x.strip()]


def seconds_or_none(value):
    try: return rule_schema.seconds(value)
    except (ValueError, TypeError): return None


def collect_issues(form):
    """Every problem a quick look at the fields finds, as (key, advice) pairs in form order, so all of
    them can be flagged at once. drafts.validated_yaml still runs afterwards and has the last word."""
    found = {}
    def add(words, key=None):
        located, advice = locate(form, words)
        found.setdefault(key or located, advice)
    text = lambda key: str(form.get(key, "")).strip()
    if not text("name"): add("name must be nonempty")
    if not text("description"): add("description must be nonempty")
    elif len(text("description")) < 20: add("description must be at least 20")
    if not text("owner"): add("owner must be nonempty")
    elif not OWNER.fullmatch(form["owner"]): add("owner must be a team")
    if form.get("status") == "retired" and form.get("enabled"): add("Retired rules must be disabled")
    if not text("query"): add("query must be nonempty")
    elif text("query").endswith("|"): add("dangling pipe")
    else:  # Only what KQL always rejects; the line under the query box shows the warnings too.
        errors = kql_check.check(form["query"]).errors
        if errors: found.setdefault("query", str(errors[0]) + (f" ({len(errors) - 1} more under the query box)"
                                                               if len(errors) > 1 else ""))
    if form.get("kind") != "NRT":
        frequency, period = seconds_or_none(form.get("queryFrequency")), seconds_or_none(form.get("queryPeriod"))
        if frequency is None or not 300 <= frequency <= MAX_SCHEDULE:
            found.setdefault("queryFrequency", SCHEDULE_ADVICE); frequency = None
        if period is None or period > MAX_SCHEDULE or (frequency is not None and period < frequency):
            found.setdefault("queryPeriod", SCHEDULE_ADVICE)
        if not text("triggerThreshold").isdigit(): add("triggerThreshold must be")
    tactics, techniques = entries(form.get("tactics", "")), entries(form.get("techniques", ""))
    if any(x not in rule_schema.TACTICS for x in tactics): add("Unknown MITRE tactic")
    if any(not TECHNIQUE.fullmatch(x) or x == "T0000" for x in techniques): add("techniques accepts parent IDs")
    for tech in entries(form.get("subTechniques", "")):
        if not SUB_TECHNIQUE.fullmatch(tech): add("subTechniques requires IDs")
        elif tech.split(".")[0] not in techniques: add("Include the parent technique")
    if not form.get("allow_missing_mitre"):
        if not tactics: add("Missing MITRE metadata")
        if not techniques: add("Missing MITRE metadata", "techniques")
    return list(found.items())


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


def tactic_label(name):
    """'DefenseEvasion' -> 'Defense Evasion', 'CommandAndControl' -> 'Command and Control' (drafts.tactic_name)."""
    return drafts.tactic_name(name)


def short_label(label):
    """'Owner (team identifier)' -> 'Owner', for lists of fields."""
    return re.sub(r"\s*\(.*\)$", "", label)


def plural(count, word):
    return f"{count} {word}" + ("" if count == 1 else "s")


def last_period(seconds):
    """86400 -> 'the last day', 14400 -> 'the last 4 hours': the time range a test query covers."""
    words = kit.duration_text(query_runner.iso_duration(seconds))
    return "the last " + (words[2:] if words.startswith("1 ") and " " not in words[2:] else words)


def elapsed_text(seconds):
    return f"{seconds:.1f} s" if seconds < 60 else f"{int(seconds // 60)} min {int(seconds % 60)} s"


def column_widths(tree, columns, rows):
    """Starting widths for a test result's columns: each heading's or longest value's, kept between
    90 and 320 (96-DPI px) so a long value doesn't push the other columns out of sight."""
    measure, most = kit.font(tree, "body").measure, px(320)
    widths = []
    for index, name in enumerate(columns):
        # Measured, not counted: 'megan.bowen' is wider than 'diego.silva'. A value this long fills the cap anyway.
        wide = max([measure(name)] + [most if len(row[index]) > 80 else measure(row[index]) for row in rows])
        widths.append(max(px(90), min(most, wide + px(28))))
    return widths


class RuleBuilderPage:
    def __init__(self, app, parent, data_dir):
        self.app, self.data_dir = app, Path(data_dir)
        self.loading = True
        self.draft_path = None
        self.source = None
        self.source_uploaded = False  # Source was matched from an uploaded rule file, not a library edit.
        self.submitted_url = None
        self.vars, self.texts = {}, {}
        self.widgets, self.labels = {}, {}
        self.flagged, self.flag_labels = {}, {}  # Field key -> advice, and the advice label under the field.
        self.quick_flags = set()  # Flagged by collect_issues, so any edit that resolves them clears them.
        self.github_user, self.github_checking = None, False
        self.saved = None
        enabled = lambda: not app.busy
        # The toolbar sits on the page canvas, so its quiet buttons take the canvas colour, not the card's.
        ground = COLORS["canvas"]
        style = ttk.Style(app.root)
        style.configure("Canvas.Small.Ghost.TButton", background=ground, bordercolor=ground, lightcolor=ground,
                        darkcolor=ground, focuscolor=ground)
        for option in ("background", "lightcolor", "darkcolor"):
            style.map("Canvas.Small.Ghost.TButton", **{option: [("disabled", ground), ("pressed", COLORS["accent_line"]),
                                                                ("active", COLORS["accent_soft"])]})
        style.map("Canvas.Small.Ghost.TButton", bordercolor=[("disabled", ground), ("focus", COLORS["accent"])])

        toolbar = self.toolbar = ttk.Frame(parent); toolbar.pack(fill="x", pady=(0, px(12)))
        toolbar.columnconfigure(1, weight=1)
        self.toolbar_left, self.toolbar_right = ttk.Frame(toolbar), ttk.Frame(toolbar)
        self.toolbar_left.grid(row=0, column=0, sticky="w")
        self.open_menu = tk.Menu(app.root, tearoff=0, font=FONTS["body"], bg=COLORS["card"], fg=COLORS["ink"], bd=0,
                                 activebackground=COLORS["accent_soft"], activeforeground=COLORS["ink"])
        for title, command, tip in (("New rule", self.new, "Start an empty rule (Ctrl+N)"),
                                    ("Edit existing rule…", self.app.open_rule_library, "Pick a rule from the library to change"),
                                    ("Open…", self.show_open_menu, "Recent drafts, or a rule file from disk"),
                                    ("Save draft", self.save, "Keep this draft on this PC (Ctrl+S)")):
            button = app.button(self.toolbar_left, title, command, "Small.Secondary.TButton")
            button.pack(side="left", padx=(0, px(6))); kit.tooltip(button, tip)
            if title == "Open…": self.open_button = button
        for title, command, tip in (("Export rule file…", self.export, "Check the rule, then save its file on this PC"),
                                    ("My submissions…", self.review_history, "Rules you submitted and their approval status")):
            button = app.button(self.toolbar_right, title, command, "Canvas.Small.Ghost.TButton")
            button.pack(side="left", padx=(0, px(2))); kit.tooltip(button, tip)
        self.github_message = tk.StringVar()  # The GitHub check's full explanation, shown on the pill's tooltip.
        self.github_pill = kit.Pill(self.toolbar_right, "GitHub: not checked")
        self.github_pill.pack(side="left", padx=(px(8), 0)); kit.tooltip(self.github_pill, "")
        self.github_message.trace_add("write", lambda *args: setattr(self.github_pill, "tooltip_text", self.github_message.get()))
        self.github_action = ttk.Button(self.toolbar_right, style="Canvas.Small.Ghost.TButton")  # Packed when needed.
        # Re-flow when the window resizes, and when the GitHub status changes the right-hand group's width.
        toolbar.bind("<Configure>", self.flow_toolbar, add="+")
        self.toolbar_right.bind("<Configure>", self.flow_toolbar, add="+")
        self.flow_toolbar()
        self.note = tk.StringVar()
        self.banner = kit.Banner(parent, self.note)
        self.banner.pack(fill="x", pady=(0, kit.space("lg")))
        self.note.trace_add("write", lambda *args: self.banner.tone(note_tone(self.note.get())))

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
        self.stepper = kit.Stepper(steps_card.body, self.steps, STEPS, enabled)
        self.stepper.pack(fill="x")
        basics, query, mitre, advanced, review = self.pages = pages

        self.field(basics, "Rule name", "name")
        self.textbox(basics, "Description", "description", 4, "Explain what this detects and why — at least 20 characters.")
        kit.field_label(basics, "Severity")
        self.choice(basics, "severity", ("Informational", "Low", "Medium", "High"), "Severity")
        # A short label leaves the hint room for its example at the smallest window size.
        self.pair(basics, ("Owner", "owner", None, "Team ID, for example soc-team"), ("Lifecycle", "status", LIFECYCLES))
        kit.field_label(basics, "Rule type")
        self.choice(basics, "kind", ("Scheduled", "NRT"), "Rule type", labels={"NRT": "Near real-time"})
        flags = ttk.Frame(basics, style="Card.TFrame"); flags.pack(fill="x", pady=(0, px(6)))
        self.check(flags, "Enable the rule when it is deployed", "enabled")
        self.check(flags, "Create incidents from alerts", "createIncident")
        self.field(basics, "Rule ID", "id", readonly=True, hint="Kept when you edit a rule; New rule creates a new one")

        # The query keeps its natural height, so the schedule below it stays in view at the smallest window size.
        self.textbox(query, "Query (KQL)", "query", QUERY_LINES, label_hint="Runs against the client's workspace after approval",
                     font="mono", expand=False,
                     placeholder='SigninLogs\n| where ResultType != "0"\n| summarize Attempts = count() by UserPrincipalName')
        self.kql_panel(query)
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
                         "threshold.")
        self.test_panel(query)

        self.tactics(mitre)
        self.field(mitre, "Parent techniques", "techniques", hint="For example T1562, T1003")
        self.field(mitre, "Sub-techniques", "subTechniques", hint="For example T1562.004 — include its parent above")
        self.check(mitre, "Allow missing MITRE details for a migrated rule", "allow_missing_mitre")
        self.hint(mitre, "This only relaxes this check. Each client the rule is assigned to must also allow it.")

        self.hint(advanced, "Optional properties such as entityMappings, customDetails, suppression and grouping, "
                            "in the rule file's format. Existing properties are kept. Leave {} for a simple rule.")
        self.textbox(advanced, "Additional rule properties", "advanced", 16, font="mono")

        self.hint(review, "Select Check rule to build the file that will be submitted. The check covers the rule's "
                          "format, not the query results (Run test query on Query & schedule shows those) or "
                          "deployment permissions.")
        self.preview = self.make_text(review, 19, readonly=True,
                                      placeholder="The rule file appears here once the check passes.")

        self.footer = kit.Card(app.footer_slot, padding=12)
        self.footer_pad = (kit.space("page"), kit.space("page"))
        actions = self.footer.body
        self.back_button = app.button(actions, "←  Back", lambda: self.step_by(-1))
        self.next_button = app.button(actions, "Next  →", lambda: self.step_by(1))
        kit.tooltip(self.back_button, "Previous step (Ctrl+Page Up)"); kit.tooltip(self.next_button, "Next step (Ctrl+Page Down)")
        submit = app.button(actions, "Submit for approval", self.review, "Primary.TButton"); submit.pack(side="right")
        app.button(actions, "Check rule", self.validate).pack(side="right", padx=(0, px(8)))
        submit.lift()  # Tab order follows the row: Check rule, then Submit for approval.
        # The footer lines up with the content column above it, whatever the window width.
        self.canvas = next(canvas for canvas, content in zip(app.page_canvases, app.page_contents) if content is parent)
        app.footer_slot.bind("<Configure>", self.align_footer, add="+")
        self.canvas.bind("<Configure>", self.align_footer, add="+")
        for sequence, step in (("<Control-Next>", 1), ("<Control-Prior>", -1)):
            app.root.bind(sequence, lambda event, s=step: self.step_key(s) if app.current_page() == "builder" else None, add="+")
        self.steps.bind("<<NotebookTabChanged>>", self.step_changed, add="+")
        self.vars["kind"].trace_add("write", self.kind_changed)
        self.load_form(drafts.new_form())
        self.step_changed()
        self.autosaved, self.recovery_offered = None, False
        self.recovery_waiting = False  # A copy offered at start-up and put off (Esc): kept for the next start.
        self.autosave_timer = None; self.start_autosave()

    # ------------------------------------------------------------ widgets
    def set_footer_visible(self, visible):
        """Pin the footer under the builder only. The slot itself is hidden elsewhere: Tk keeps an
        emptied pack master at its last size, which would leave a blank band above the status bar."""
        slot, frame = self.app.footer_slot, self.footer.frame
        if visible:
            if not slot.winfo_manager(): slot.pack(side="bottom", fill="x", before=self.app.tabs)
            if not frame.winfo_manager(): frame.pack(fill="x", padx=self.footer_pad, pady=(0, kit.space("md")))
            self.app.root.after_idle(self.align_footer)
        else:
            frame.pack_forget(); slot.pack_forget()

    def align_footer(self, event=None):
        """Give the footer the same left and right edges as the content column (fit_page centres a capped
        column on wide windows, and the page scrollbar narrows the canvas)."""
        canvas, slot = self.canvas, self.app.footer_slot
        items = canvas.find_all()
        if not items or not self.footer.frame.winfo_manager(): return
        x = canvas.winfo_rootx() - slot.winfo_rootx() + int(canvas.coords(items[0])[0])
        column = int(float(canvas.itemcget(items[0], "width")))
        pad = (max(0, x + kit.space("page")), max(0, slot.winfo_width() - x - column + kit.space("page")))
        if slot.winfo_width() > 1 and pad != self.footer_pad:
            self.footer_pad = pad
            self.footer.frame.pack_configure(padx=pad)

    def flow_toolbar(self, event=None):
        """Keep the toolbar on one row while it fits, and move its right-hand group under it when it doesn't.
        The query box then gives up that row's height, so the schedule stays in view at the smallest size."""
        left, right = self.toolbar_left, self.toolbar_right
        width = self.toolbar.winfo_width()
        wraps = width > 1 and left.winfo_reqwidth() + right.winfo_reqwidth() + px(16) > width
        place = dict(row=1, column=0, columnspan=2, sticky="w", pady=(px(8), 0)) if wraps else \
            dict(row=0, column=1, columnspan=1, sticky="e", pady=0)
        info = right.grid_info()
        if not info or int(info["row"]) != place["row"]: right.grid(**place)
        query = self.texts.get("query")
        if query is None: return
        lines = QUERY_LINES
        if wraps:  # Enough whole lines to cover the second row.
            line = int(query.tk.call("font", "metrics", query.cget("font"), "-linespace"))
            lines -= -(-(right.winfo_reqheight() + px(8)) // line)
        if int(query.cget("height")) != lines: query.configure(height=lines)

    def step_changed(self, event=None):
        index = self.steps.index("current")
        title, description = STEPS[index]
        self.form_card.title_var.set(title); self.form_card.subtitle_var.set(description)
        focused = self.app.root.focus_lastfor()
        self.back_button.pack_forget(); self.next_button.pack_forget()
        if index > 0: self.back_button.pack(side="left", padx=(0, px(8)))
        if index < len(STEPS) - 1:
            self.next_button.configure(text="Next: " + STEPS[index + 1][0] + "  →")
            self.next_button.pack(side="left")
        # The keyboard mustn't stay on a button that just went away: hand it to the one that stays.
        keep = self.back_button if focused is self.next_button and index == len(STEPS) - 1 else \
            self.next_button if focused is self.back_button and index == 0 else None
        if keep is not None: self.app.root.update_idletasks(); keep.focus_set()

    def step_by(self, step):
        index = self.steps.index("current") + step
        if not self.app.busy and 0 <= index < len(STEPS): self.steps.select(index)

    def step_key(self, step):
        """Ctrl+Page Down / Ctrl+Page Up: move a step and take the keyboard to its first field."""
        before = self.steps.index("current")
        self.step_by(step)
        if self.steps.index("current") != before:
            self.app.root.update_idletasks()
            target = self.pages[self.steps.index("current")].tk_focusNext()
            if target is not None: target.focus_set()
        return "break"

    def tactics(self, parent):
        """MITRE tactics as a grid of check boxes, kept in step with the comma-separated vars['tactics']
        that drafts read and write. Values that aren't tactics are listed with a way to remove them."""
        kit.field_label(parent, "Tactics", "Tick every tactic the rule detects")
        grid = tk.Frame(parent, bg=COLORS["card"], bd=0, highlightthickness=1, highlightbackground=COLORS["line"],
                        highlightcolor=COLORS["line"], padx=px(12), pady=px(6))
        grid.pack(fill="x", pady=(0, px(14)))
        var = tk.StringVar(); self.vars["tactics"] = var
        names = sorted(rule_schema.TACTICS)
        self.tactic_vars, self.tactic_boxes, self.tactic_columns = {}, [], None
        for name in names:
            ticked = self.tactic_vars[name] = tk.BooleanVar()
            box = ttk.Checkbutton(grid, text=tactic_label(name), variable=ticked)
            self.tactic_boxes.append(box); self.app.inputs.append(box)
        grid.focus_target = self.tactic_boxes[0]
        self.flow_tactics(TACTIC_COLUMNS, grid)
        grid.bind("<Configure>", lambda event: self.flow_tactics(None, grid), add="+")
        self.unknown_tactics = ttk.Frame(parent, style="Card.TFrame")  # Packed under the grid when needed.
        self.unknown_text = tk.StringVar()
        kit.wrap_to_parent(ttk.Label(self.unknown_tactics, textvariable=self.unknown_text, style="Hint.TLabel",
                                     justify="left", width=1)).pack(side="left", fill="x", expand=True)
        remove = ttk.Button(self.unknown_tactics, text="Remove", style="Small.Ghost.TButton", command=self.remove_unknown_tactics)
        remove.pack(side="right", padx=(px(8), 0)); self.app.inputs.append(remove)
        guard = {"busy": False}
        def compose(*args):
            if guard["busy"]: return
            guard["busy"] = True
            try:
                kept = [x for x in entries(var.get()) if x not in self.tactic_vars or self.tactic_vars[x].get()]
                var.set(", ".join(kept + [x for x in names if self.tactic_vars[x].get() and x not in kept]))
            finally: guard["busy"] = False
        def decompose(*args):
            current = entries(var.get())
            unknown = list(dict.fromkeys(x for x in current if x not in rule_schema.TACTICS))
            self.unknown_text.set(("Not a MITRE tactic: " if len(unknown) == 1 else "Not MITRE tactics: ") + ", ".join(unknown))
            if unknown and not self.unknown_tactics.winfo_manager():
                self.unknown_tactics.pack(fill="x", after=grid, pady=(0, px(14)))
            elif not unknown: self.unknown_tactics.pack_forget()
            if guard["busy"]: return
            guard["busy"] = True
            try:
                for name, ticked in self.tactic_vars.items():
                    if ticked.get() != (name in current): ticked.set(name in current)
            finally: guard["busy"] = False
        for ticked in self.tactic_vars.values(): ticked.trace_add("write", compose)
        var.trace_add("write", decompose)
        self.widgets["tactics"], self.labels["tactics"] = grid, "Tactics"
        var.trace_add("write", lambda *args: self.changed("tactics"))

    def flow_tactics(self, columns, grid):
        """Lay the tactic boxes out down each column, then across, so the names read alphabetically: in
        three columns, or two when the window is too narrow for the longest name."""
        if columns is None:
            room = grid.winfo_width() - 2 * (px(12) + int(grid.cget("highlightthickness")))
            widest = max(box.winfo_reqwidth() for box in self.tactic_boxes) + px(12)
            columns = max(2, min(TACTIC_COLUMNS, room // widest))
        if columns == self.tactic_columns: return
        self.tactic_columns, rows = columns, -(-len(self.tactic_boxes) // columns)
        for index, box in enumerate(self.tactic_boxes):
            box.grid(row=index % rows, column=index // rows, sticky="w", padx=(0, px(12)))
        for column in range(TACTIC_COLUMNS):
            grid.columnconfigure(column, weight=int(column < columns), uniform="tactics" if column < columns else "")

    def remove_unknown_tactics(self):
        if not self.app.busy:
            self.vars["tactics"].set(", ".join(x for x in entries(self.vars["tactics"].get()) if x in rule_schema.TACTICS))

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
            control.bind("<MouseWheel>", lambda event: "break")  # Scrolling the page must not change a selection.
            def to_value(*args):
                for value, text in pairs:
                    if text == shown.get() and var.get() != value: var.set(value)
            def to_text(*args):
                text = next((text for value, text in pairs if value == var.get()), var.get())
                if shown.get() != text: shown.set(text)
            shown.trace_add("write", to_value); var.trace_add("write", to_text)
        else:
            control = ttk.Entry(frame, textvariable=var, state="readonly" if readonly else "normal")
        control.pack(fill="x"); self.app.inputs.append(control)
        self.widgets[key], self.labels[key] = control, label
        var.trace_add("write", lambda *args: self.changed(key))

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
        units.pack(side="left", padx=(px(8), 0)); units.bind("<MouseWheel>", lambda event: "break")
        self.app.inputs.extend((spin, units))
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
        var.trace_add("write", lambda *args: self.changed(key))

    def choice(self, parent, key, options, label, labels=None):
        var = tk.StringVar(); self.vars[key] = var
        control = kit.Segmented(parent, var, [(value, (labels or {}).get(value, value)) for value in options])
        control.pack(anchor="w", pady=(0, px(14)))
        self.widgets[key], self.labels[key] = control, label
        var.trace_add("write", lambda *args: self.changed(key))

    def pair(self, parent, left, right):
        row = ttk.Frame(parent, style="Card.TFrame"); row.pack(fill="x")
        for index, spec in enumerate((left, right)):
            row.columnconfigure(index, weight=1, uniform="fields")
            col = ttk.Frame(row, style="Card.TFrame"); col.grid(row=0, column=index, sticky="new", padx=(0, px(12)) if not index else 0)
            self.field(col, *spec)

    def check(self, parent, label, key):
        var = tk.BooleanVar(); self.vars[key] = var
        control = ttk.Checkbutton(parent, text=label, variable=var)
        control.pack(anchor="w", pady=(0, px(6))); self.app.inputs.append(control)
        self.widgets[key], self.labels[key] = control, label
        var.trace_add("write", lambda *args: self.changed(key))

    def make_text(self, parent, height, readonly=False, font="mono", placeholder=None, expand=True):
        frame, text = kit.text_area(parent, height, readonly=readonly, undo=not readonly, font=font, placeholder=placeholder)
        frame.pack(fill="both" if expand else "x", expand=expand, pady=(0, px(14)))
        if not readonly: self.app.inputs.append(text)
        return text

    def textbox(self, parent, label, key, height, hint=None, font="body", placeholder=None, label_hint=None, expand=True):
        kit.field_label(parent, label, label_hint)
        if hint: self.hint(parent, hint)
        text = self.make_text(parent, height, font=font, placeholder=placeholder, expand=expand); self.texts[key] = text
        self.widgets[key], self.labels[key] = text, label
        text.bind("<<Modified>>", lambda event: self.text_changed(text, key), add="+")  # The kit's placeholder listens too.

    def text_changed(self, text, key):
        if text.edit_modified():
            text.edit_modified(False)
            if key == "query": self.queue_kql()
            self.changed(key)

    # ------------------------------------------------------------ state
    def form(self):
        return {**{k: v.get() for k, v in self.vars.items()},
                **{k: v.get("1.0", "end-1c") for k, v in self.texts.items()}}

    def dirty(self):
        return self.saved is not None and self.form() != self.saved

    def changed(self, key=None):
        """An edit (``key``) clears that field's flag, and any quick-check flag it resolved elsewhere (Lifecycle
        for Enabled, say); flags from the full check wait for their own field. A new form (None) clears them all."""
        if self.loading: return
        if key is None: self.clear_test()  # Another rule: the last one's results don't apply.
        self.sync_test()
        self.clear_flag(key)
        if key == "kind":
            for name in drafts.SCHEDULE_FIELDS: self.clear_flag(name)
        stale = [name for name in self.flagged if name in self.quick_flags]
        if stale:
            still = dict(collect_issues(self.form()))
            for name in stale:
                if name not in still: self.clear_flag(name)
        self.render_issues()  # Also drops the ticks a passed check left on the steps.
        self.submitted_url = None
        self.banner.set_action()  # A previous rule's "Open in GitHub" no longer applies.
        kit.set_text(self.preview, "")
        self.note.set(self.issue_note() or ("Unsaved changes — select Check rule again before submitting." if self.dirty()
                                            else "Local draft. Select Check rule when it's ready, then Submit for approval."))
        self.render_state()

    def kind_changed(self, *args):
        if self.vars["kind"].get() == "NRT": self.schedule.pack_forget()
        elif not self.schedule.winfo_manager(): self.schedule.pack(fill="x", after=self.kql_row)

    def render_state(self):
        if self.submitted_url: self.state_pill.set("Submitted", "success")
        elif self.preview.get("1.0", "end-1c"): self.state_pill.set("Checked", "success")
        elif self.dirty(): self.state_pill.set("Draft", "info")  # The banner already says the changes aren't saved.
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
        self.changed(); self.check_kql(); self.steps.select(0)

    def can_discard(self):
        return not self.dirty() or kit.confirm(self.app.root, "Discard changes?",
            "This rule has unsaved changes. Discard them? To keep them, select Keep editing, then Save draft.",
            ok="Discard", cancel="Keep editing", destructive=True)

    def new(self):
        if not self.app.busy and self.can_discard(): self.load_form(drafts.new_form())

    def error(self, error):
        self.set_preview(""); self.note.set("Needs attention: " + str(error))

    # ------------------------------------------------------------ files
    def recent_drafts(self):
        folder = self.data_dir / "drafts"
        if not folder.exists(): return []
        files = sorted((p for p in folder.glob("*.rule-draft.json") if p.is_file() and p.name != RECOVERY_NAME),
                       key=lambda p: p.stat().st_mtime, reverse=True)
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

    def is_recovery(self, path):
        """Whether ``path`` is the recovery copy itself (never a draft to save into)."""
        try: return bool(path) and Path(path).resolve() == self.recovery_path().resolve()
        except (OSError, ValueError): return False

    def open_path(self, path, draft):
        try:
            form = drafts.read_draft(path) if draft else drafts.read_rule(path)
            source = drafts.read_draft_source(path) if draft else None
            if not self.can_discard(): return
            if draft and self.is_recovery(path):
                # The recovery copy opens as unsaved work: Save draft gives it a draft of its own.
                self.load_form(form, None, source); self.saved = {}; self.render_state()
                self.note.set("Opened the recovery copy. Select Save draft to keep it as a draft.")
                return
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
        self.app.work("Checking whether this rule is already in the rule library…", task, done, page="builder",
                      failed="Couldn't check the rule library")

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
            if self.is_recovery(self.draft_path): self.draft_path = None  # Saving there would delete it straight after.
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
            self.discard_recovery()
        except (ValueError, OSError) as error: self.error(error)

    # ------------------------------------------------------------ recovery
    def recovery_path(self):
        return self.data_dir / "drafts" / RECOVERY_NAME

    def autosave(self, schedule=True):
        """Every AUTOSAVE_MS, copy unsaved work to the recovery draft, so a crash or a restart loses at most
        that much; a form with nothing unsaved removes the copy. The timer is registered on the root.
        Once the app is closing normally (nothing unsaved, or the changes discarded), nothing is copied."""
        if getattr(self.app, "closing", False): self.autosave_timer = None; return
        if schedule: self.autosave_timer = self.app.root.after(AUTOSAVE_MS, self.autosave)
        try:
            if not self.dirty():
                # A copy put off at start-up waits for the next start; nothing new needs its place yet.
                if not self.recovery_waiting: self.discard_recovery()
                return
            form, path = self.form(), self.recovery_path()
            if form == self.autosaved and path.exists(): return
            path.parent.mkdir(parents=True, exist_ok=True)
            drafts.atomic_write(path, json.dumps({
                "format": "sentinel-rule-draft", "version": 1, "form": form, "source": self.source,
                "recovery": {"draft_path": self.draft_path, "saved_at": datetime.now(timezone.utc).isoformat()}},
                indent=2) + "\n")
            self.autosaved, self.recovery_waiting = form, False  # New unsaved work takes the copy's place.
        except (OSError, ValueError, TypeError) as error:
            self.app.log("Couldn't keep a recovery copy of the rule: " + str(error))

    def start_autosave(self):
        self.stop_autosave(); self.autosave_timer = self.app.root.after(AUTOSAVE_MS, self.autosave)

    def stop_autosave(self):
        if self.autosave_timer is not None:
            self.app.root.after_cancel(self.autosave_timer); self.autosave_timer = None

    def discard_recovery(self):
        if self.recovery_waiting: return  # Put off at start-up (Esc): it is offered again at the next start.
        self.autosaved = None
        try: self.recovery_path().unlink(missing_ok=True)
        except OSError: pass

    def offer_recovery(self):
        """At start-up, offer the recovery draft once, when it is newer than the draft it was copied from."""
        path = self.recovery_path()
        if self.recovery_offered or not path.is_file(): return False
        self.recovery_offered = True
        try:
            form, source = drafts.read_draft(path), drafts.read_draft_source(path)
            original = (json.loads(drafts.read_bounded(path)).get("recovery") or {}).get("draft_path")
            original = Path(original) if isinstance(original, str) and original and not self.is_recovery(original) else None
            kept = path.stat().st_mtime
            if original is not None and original.is_file() and original.stat().st_mtime >= kept:
                self.discard_recovery(); return False  # Saved normally after the copy was made.
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            self.discard_recovery(); return False
        when = datetime.fromtimestamp(kept).strftime("%d %b %Y at %H:%M")
        name = "“" + form["name"].strip() + "”" if form["name"].strip() else "an untitled rule"
        answer = kit.confirm(self.app.root, "Restore unsaved rule?",
                             f"Sentinel Workspace closed before your changes to {name} were saved. Restore the copy "
                             f"kept on {when}?", ok="Restore", cancel="Discard", closed=None)
        if answer is None:  # Esc or the window's X: not now. Only Discard deletes the work.
            self.recovery_waiting = True; return False
        if not answer:
            self.discard_recovery(); return False
        keep = original if original is not None and original.is_file() else None
        self.load_form(form, str(keep) if keep else None, source)
        try: self.saved = drafts.read_draft(keep) if keep else {}  # Unsaved compared with the last save.
        except (OSError, ValueError): self.saved = {}
        self.render_state()
        self.note.set("Restored your unsaved changes from " + when + ". Select Save draft to keep them.")
        self.app.show_page("builder")
        return True

    # ------------------------------------------------------------ checks
    def validate(self):
        """Flag every field a quick look finds wrong at once; when there are none, run the full check."""
        if self.app.busy: return None
        self.clear_flag(); self.stepper.clear_status()
        issues = collect_issues(self.form())
        self.quick_flags = {key for key, _ in issues}
        if issues: self.show_issues(issues); return None
        try:
            text, warnings = drafts.validated_yaml(self.form())
        except (ValueError, OSError, TypeError, RecursionError) as error: self.problem(error); return None
        self.set_preview(text)
        for index in range(len(STEPS) - 1): self.stepper.set_status(index, "done")
        self.steps.select(len(STEPS) - 1)
        self.note.set("Rule check passed. " + ("; ".join(warnings) + ". Each assigned client must allow this. " if warnings else "")
                      + "Select Submit for approval when you're ready. The query is tested against the workspace after approval.")
        return text, warnings

    def step_of(self, key):
        widget = self.widgets[key]
        while widget is not None and widget not in self.pages: widget = widget.master
        return self.pages.index(widget) if widget is not None else 0

    def problem(self, error):
        """Show a failed full check next to the field it is about, instead of a dialog."""
        key, advice = locate(self.form(), str(error))
        if key is None:
            self.set_preview(""); self.note.set("Needs attention: " + advice); return
        self.show_issues([(key, advice)])

    def show_issues(self, issues):
        """Outline each field in ``issues`` with its advice, mark their steps and open the first one."""
        self.set_preview("")
        self.steps.select(self.step_of(issues[0][0]))
        for index, (key, advice) in enumerate(issues): self.flag(key, advice, focus=not index)
        self.render_issues()
        self.note.set(self.issue_note())

    def render_issues(self):
        """Mark each step that holds flagged fields with their count; every other step goes back to plain."""
        counts = {}
        for key in self.flagged: counts[self.step_of(key)] = counts.get(self.step_of(key), 0) + 1
        self.stepper.clear_status()
        for index, count in counts.items(): self.stepper.set_status(index, "error", plural(count, "issue"))

    def issue_note(self):
        """The banner text for the fields still flagged, or None."""
        if not self.flagged: return None
        if len(self.flagged) == 1:
            key, advice = next(iter(self.flagged.items())); step = self.step_of(key)
            return ("Needs attention — " + short_label(self.labels[key]) + " (step " + str(step + 1) + ", "
                    + STEPS[step][0] + "): " + advice)
        groups = {}
        for key in self.flagged: groups.setdefault(self.step_of(key), []).append(short_label(self.labels[key]))
        return ("Needs attention — " + plural(len(self.flagged), "field") + ": "
                + "; ".join(", ".join(names) + " (" + STEPS[step][0] + ")" for step, names in sorted(groups.items())))

    def flag(self, key, advice="", focus=True):
        """Outline one field in red with its advice underneath; ``focus`` also scrolls to it and focuses it."""
        self.clear_flag(key)
        widget = self.widgets[key]
        # Repeat the advice under the field: the banner may be scrolled out of view.
        anchor = widget.master if isinstance(widget, (tk.Text, ttk.Spinbox)) else widget
        if key == "tactics" and self.unknown_tactics.winfo_manager(): anchor = self.unknown_tactics  # Under its Remove.
        width = anchor.winfo_width() if anchor.winfo_width() > 1 else self.steps.winfo_width() // 2
        # width=1: the label wraps to the width it is given, so its text never widens the field's column.
        label = tk.Label(anchor.master, text=advice, font=FONTS["small"], fg=COLORS["danger"], bg=COLORS["card"],
                         justify="left", anchor="w", bd=0, width=1, wraplength=max(px(120), width))
        label.pack(fill="x", after=anchor, pady=(px(4), px(8)))
        kit.wrap_to_parent(label, minimum=px(120))
        self.flagged[key], self.flag_labels[key] = advice, label
        self.outline(widget, True)
        if focus:
            self.app.scroll_into_view(widget, only_if_hidden=True)
            try: getattr(widget, "focus_target", widget).focus_set()
            except tk.TclError: pass

    def clear_flag(self, key=None):
        """Remove the flag from one field, or from every field when ``key`` is None."""
        for name in list(self.flagged) if key is None else [key] if key in self.flagged else []:
            del self.flagged[name]
            self.flag_labels.pop(name).destroy()
            self.outline(self.widgets[name], False)

    @staticmethod
    def outline(widget, error):
        if isinstance(widget, (ttk.Entry, ttk.Combobox, ttk.Spinbox)):
            widget.configure(style=("Error." if error else "") + widget.winfo_class())
        elif isinstance(widget, tk.Text):
            widget.master.error = error
            widget.master.configure(highlightbackground=COLORS["danger" if error else "line"], highlightthickness=2 if error else 1)
        elif isinstance(widget, tk.Frame):  # A segmented choice, or the tactic grid.
            widget.configure(highlightbackground=COLORS["danger" if error else "line"], highlightthickness=2 if error else 1)

    # ------------------------------------------------------------ KQL check
    def kql_panel(self, parent):
        """Make the query box an editor (kql_editor: colours, completions while typing) with a line under
        it from kql_check, updated as the query is typed. Each problem is underlined in the query itself
        (red for what KQL always rejects, amber for likely slips); beside the line, a link puts in the
        spellings the check suggests, and another chooses the box's colours."""
        text = self.texts["query"]
        text.master.pack_configure(pady=(0, px(4)))  # The line belongs to the box above it.
        self.kql_job = None
        self.kql_report = None
        self.editor = KqlEditor(text, self.app.ui_state.get("kql_theme"))
        self.editor.changed = self.check_kql
        card = COLORS["card"]
        row = self.kql_row = tk.Frame(parent, bg=card, bd=0)
        # The links' focus rings make the row 2px taller than its line of text; the gap under it gives them back.
        row.pack(fill="x", after=text.master, pady=(0, px(12) - 2))
        row.columnconfigure(0, weight=1)
        # width=1: the line wraps to the width it is given instead of widening the step.
        self.kql_line = tk.Label(row, text="", font=FONTS["small"], fg=COLORS["muted"], bg=card,
                                 justify="left", anchor="w", bd=0, width=1)
        self.kql_line.grid(row=0, column=0, sticky="new")
        kit.wrap_to_parent(self.kql_line, minimum=px(160))
        tools = tk.Frame(row, bg=card, bd=0); tools.grid(row=0, column=1, sticky="ne", padx=(px(12), 0))
        self.kql_fix = kit.link(tools, "", self.fix_kql, COLORS["accent"], COLORS["accent_hover"], card)  # Packed when needed.
        kit.tooltip(self.kql_fix, "Puts in the spelling the check suggests (Ctrl+. does the one at the caret). "
                                  "Ctrl+Z undoes it.")
        self.kql_theme = kit.link(tools, "", self.show_kql_themes, COLORS["muted"], COLORS["ink"], card)
        self.kql_theme.pack(side="right")
        kit.tooltip(self.kql_theme, "Choose the Query box's theme")
        self.kql_theme_var = tk.StringVar(value=self.editor.theme)
        self.kql_theme.configure(text="Theme: " + self.editor.theme)
        self.check_kql()

    def queue_kql(self):
        """Colour the query at once, and check it once typing pauses, so each keystroke stays instant."""
        root = self.app.root
        self.editor.paint()
        if self.kql_job is not None: root.after_cancel(self.kql_job)
        self.kql_job = root.after(KQL_DELAY, self.check_kql)

    def check_kql(self):
        """Check the query now: colour it, mark each problem in the box and say what was found under it."""
        if self.kql_job is not None: self.app.root.after_cancel(self.kql_job)
        self.kql_job = None
        report = self.kql_report = self.editor.check()
        if report is None:
            line, tone = "The query is checked as you type: quotes, brackets, steps and table names.", "neutral"
        else:
            line, tone = kql_check.summary(report)
        self.kql_line.configure(text=line, fg=COLORS["muted"] if tone == "neutral" else TONES[tone][0])
        fixes = self.editor.fixes()
        self.kql_fix.configure(text=fix_label(fixes))
        if fixes and not self.kql_fix.winfo_manager(): self.kql_fix.pack(side="left", padx=(0, px(14)))
        elif not fixes: self.kql_fix.pack_forget()
        return report

    def fix_kql(self):
        """Put in every spelling the check suggests (the link under the query box)."""
        if not self.app.busy and self.editor.apply(self.editor.fixes()): self.texts["query"].focus_set()

    def show_kql_themes(self):
        """The colour schemes, as a menu under the link."""
        menu = self.kql_theme_menu = kit.popup_menu(self.app.root)
        for name in KQL_THEMES:
            menu.add_radiobutton(label=name, value=name, variable=self.kql_theme_var,
                                 command=lambda n=name: self.set_kql_theme(n))
        link = self.kql_theme
        try: menu.tk_popup(link.winfo_rootx(), link.winfo_rooty() + link.winfo_height())
        finally: menu.grab_release()

    def set_kql_theme(self, name):
        """Draw the query box in the colour scheme ``name`` and remember it (ui-state kql_theme)."""
        self.editor.set_theme(name)
        self.kql_theme_var.set(self.editor.theme)
        self.kql_theme.configure(text="Theme: " + self.editor.theme)
        self.app.ui_state["kql_theme"] = self.editor.theme

    # ------------------------------------------------------------ test query
    def test_panel(self, parent):
        """Run test query: the draft's KQL, read-only, against the workspace chosen on Connect (query_runner).
        The results stay until the next run or a new rule; a change to the query, Look back or workspace marks
        them out of date rather than clearing them, so they can be compared with the change."""
        self.tested = None        # (session, query, seconds, workspace ID) the results shown came from.
        self.test_outcome = None  # (pill text, tone, summary) for those results.
        self.test_raw = ""        # Azure's full message for the last failure, for Copy details.
        kit.section_label(parent, "Test the query")
        row = ttk.Frame(parent, style="Card.TFrame"); row.pack(fill="x", pady=(0, px(12)))
        self.test_button = ttk.Button(row, text="Run test query", style="Secondary.TButton", command=self.run_test)
        self.test_button.pack(side="left", anchor="n")
        kit.tooltip(self.test_button, "")
        self.test_hint = tk.StringVar()
        # width=1: the label wraps to the room beside the button instead of widening the row.
        hint = ttk.Label(row, textvariable=self.test_hint, style="Hint.TLabel", justify="left", width=1)
        hint.pack(side="left", fill="x", expand=True, padx=(px(12), 0), pady=(px(1), 0))
        kit.wrap_to_parent(hint, minimum=px(160))
        self.test_result = ttk.Frame(parent, style="Card.TFrame")  # Packed once a test has run.
        head = ttk.Frame(self.test_result, style="Card.TFrame"); head.pack(fill="x", pady=(0, px(8)))
        self.test_pill = kit.Pill(head, "")
        self.test_pill.pack(side="left", anchor="n")
        self.test_summary = tk.StringVar()
        summary = ttk.Label(head, textvariable=self.test_summary, style="Muted.TLabel", justify="left", width=1)
        summary.pack(side="left", fill="x", expand=True, padx=(px(10), 0))
        kit.wrap_to_parent(summary, minimum=px(160))
        self.test_message = tk.StringVar()
        self.test_banner = kit.Banner(self.test_result, self.test_message, tone="danger")  # Packed for a problem.
        self.test_frame = self.test_tree = None  # A table built for each result's columns.
        self.texts["query"].bind("<Shift-Return>", self.test_key)
        self.sync_test()

    def test_blocked(self):
        """Why a test query can't run now (no workspace, or an Azure Government one), or None."""
        app = self.app
        if app.session is None or not app.workspace:
            return "Sign in on Connect and choose a workspace to test the query here."
        if getattr(app.session, "cloud", None) == GOV_CLOUD:
            return "Test queries aren't available for Azure Government (GCC High) workspaces yet."
        return None

    def test_seconds(self):
        return query_runner.test_window(self.vars["kind"].get(), self.vars["queryPeriod"].get())

    def sync_test(self):
        """Enable Run test query only with a public-cloud workspace and no task running, say what it will do
        (or why it can't), and mark results out of date once the query, Look back or workspace has changed."""
        reason, app = self.test_blocked(), self.app
        self.test_button.configure(state="disabled" if reason or app.busy else "normal")
        self.test_button.tooltip_text = reason or             "Run the query read-only against the workspace (Shift+Enter in the Query box)"
        if reason:
            self.test_hint.set(reason)
        else:
            seconds = self.test_seconds()
            period = seconds_or_none(self.vars["queryPeriod"].get())
            why = (" (near real-time rules have no Look back)" if self.vars["kind"].get() == "NRT" else
                   f" (Look back is {kit.duration_text(query_runner.iso_duration(period))}; tests stop at "
                   f"{kit.duration_text(query_runner.iso_duration(query_runner.MAX_PERIOD))})"
                   if period and period > seconds else "")
            self.test_hint.set(f"Runs read-only against {app.workspace['workspace_name']} over {last_period(seconds)}"
                               f"{why} and shows up to {query_runner.ROW_LIMIT} rows. Nothing is saved or deployed.")
        if self.tested is None: return
        if self.tested[0] is not app.session:  # Signed out, or another client: its results go too.
            self.clear_test(); return
        if self.test_outcome is None: return  # A failure stays as it is until the next run.
        current = (self.texts["query"].get("1.0", "end-1c"), self.test_seconds(),
                   (app.workspace or {}).get("workspace_id"))
        if current != self.tested[1:]:
            self.test_pill.set("Out of date", "neutral")
            self.test_summary.set("The query, Look back or workspace changed after this test. Run it again to see "
                                  "current results.")
        else:
            text, tone, summary = self.test_outcome
            self.test_pill.set(text, tone); self.test_summary.set(summary)

    def clear_test(self):
        self.tested = self.test_outcome = None; self.test_raw = ""
        if self.test_frame is not None: self.test_frame.destroy(); self.test_frame = self.test_tree = None
        self.test_banner.pack_forget(); self.test_result.pack_forget()

    def test_key(self, event=None):
        """Shift+Enter in the Query box, as in Log Analytics."""
        self.run_test()
        return "break"

    def run_test(self):
        if self.app.busy: return
        reason = self.test_blocked()
        query = self.texts["query"].get("1.0", "end-1c")
        try:
            if reason: raise Stop(reason)
            query_runner.capped_query(query)  # An empty query or a dangling | is explained without asking Azure.
            errors = kql_check.check(query).errors  # So is anything else KQL always rejects.
            if errors: raise Stop("Fix the query first. " + str(errors[0]))
        except Stop as error:
            self.test_failed(error, None, query); return
        session, workspace, seconds = self.app.session, dict(self.app.workspace), self.test_seconds()
        def task():
            try: return query_runner.run_test_query(session, workspace, query, seconds), None
            except AuthenticationRequired: raise  # The app offers a fresh sign-in.
            except Stop as error: return None, error
        def done(outcome):
            result, error = outcome
            if self.app.session is not session: return  # Signed out meanwhile: nothing to show it against.
            if error is not None: self.test_failed(error, workspace, query, session, seconds)
            else: self.test_passed(result, session, query, workspace)
        self.app.work("Running the test query against " + workspace["workspace_name"] + "…", task, done,
                      page="builder", failed="Couldn't run the test query")

    def show_test_result(self):
        if not self.test_result.winfo_manager():
            self.test_result.pack(fill="x", pady=(0, px(14)))
        self.app.root.update_idletasks()
        self.app.scroll_into_view(self.test_result, only_if_hidden=True)

    def test_failed(self, error, workspace, query, session=None, seconds=None):
        """Explain a test that didn't run in plain words (advice.py), with Log Analytics' own reason under it."""
        self.clear_test()
        name = (workspace or self.app.workspace or {}).get("workspace_name")
        problem, detail = getattr(error, "problem", None), getattr(error, "detail", None)
        tip = advice.lookup(problem or str(error), workspace=name)
        if tip and detail: text = tip + "\nLog Analytics: " + detail
        elif problem is None and detail: text = "Log Analytics didn't run the query: " + detail
        else: text = tip or str(error)
        better = kql_check.suggest_table(kql_check.unresolved_name(detail))
        if better: text += f"\nDid you mean {better}?"
        self.test_raw = getattr(error, "raw", None) or str(error)
        self.tested, self.test_outcome = (session or self.app.session, query, seconds, None), None
        self.test_pill.set("Didn't run", "danger")
        self.test_summary.set("Nothing was read from " + name + "." if name else "Nothing was sent to Azure.")
        self.test_message.set(text); self.test_banner.tone("danger")
        self.test_banner.set_action("Copy details", self.copy_test_details)
        self.test_banner.pack(fill="x", pady=(0, px(8)))
        self.show_test_result()
        if workspace is not None:  # It reached Azure; a local problem needs no log entry.
            self.app.log("Test query against " + workspace["workspace_name"] + " didn't run: " + self.test_raw)
            self.app.flash("The test query didn't run. The reason is under Run test query.", "warning")

    def copy_test_details(self):
        self.app.root.clipboard_clear(); self.app.root.clipboard_append(self.test_raw)
        self.app.flash("Details copied.")

    def test_passed(self, result, session, query, workspace=None):
        """Show the row count, the time taken and the first rows of a test that ran."""
        self.clear_test()
        workspace_id = (workspace or self.app.workspace or {}).get("workspace_id")
        columns, rows, more = result["columns"], result["rows"], result["more"]
        count = len(rows)
        text, tone = (f"{query_runner.ROW_LIMIT}+ rows", "info") if more else \
            ("No rows", "neutral") if not count else (plural(count, "row"), "success")
        summary = (("First " + str(count) + " rows from " if more else "From ") + last_period(result["seconds"])
                   + " in " + result["workspace_name"] + " · took " + elapsed_text(result["elapsed"]))
        self.tested, self.test_outcome = (session, query, result["seconds"], workspace_id), (text, tone, summary)
        self.test_pill.set(text, tone); self.test_summary.set(summary)
        if result["partial"]:
            self.test_message.set("Log Analytics returned only part of the results: " + result["partial"])
            self.test_banner.tone("warning"); self.test_banner.set_action()
            self.test_banner.pack(fill="x", pady=(0, px(8)))
        if columns:
            self.test_frame, self.test_tree, empty = kit.table(
                self.test_result, [("c%d" % index, name, 120, index == len(columns) - 1)
                                   for index, name in enumerate(columns)],
                height=max(TEST_ROWS[0], min(count, TEST_ROWS[1])), icon="search")
            for index, width in enumerate(column_widths(self.test_tree, columns, rows)):
                self.test_tree.column("c%d" % index, width=width)
            for values in rows: self.test_tree.insert("", "end", values=values)
            self.test_frame.pack(fill="x")
            if not count: empty.show("No rows in " + last_period(result["seconds"]) + ".")
        self.show_test_result()
        self.app.log(f"Test query against {result['workspace_name']}: {text.lower()} from {last_period(result['seconds'])}"
                     f" in {elapsed_text(result['elapsed'])}.")
        self.app.flash("Test query: " + text.lower() + " in " + elapsed_text(result["elapsed"]) + ".")

    # ------------------------------------------------------------ GitHub sign-in
    @staticmethod
    def github_probe():
        return GitHubReader.get("user")["login"]

    def show_github(self, text, tone, message, action=None, command=None):
        """Show the GitHub check in the toolbar: a status pill (its tooltip explains) and, when the
        analyst can do something about it, one action beside it."""
        self.github_pill.set(text, tone)
        self.github_message.set(message)
        if action:
            self.github_action.configure(text=action, command=command)
            if not self.github_action.winfo_manager(): self.github_action.pack(side="left", padx=(px(4), 0))
        else:
            self.github_action.pack_forget()

    def check_github(self, force=False):
        """Make sure submitting will work before the analyst reaches the last step."""
        if self.github_checking or (self.github_user and not force): return
        repository = self.app.github_repository()
        if "/" not in repository:
            self.github_user = None
            self.app.github_pill.set("Not set")
            self.show_github("Rule library not set", "warning", "Set your team's rule library on Connect before "
                             "submitting rules. You can still write and check rules.", "Go to Connect", self.app.show_github_settings)
            return
        self.github_checking = True
        self.show_github("GitHub: checking…", "info", "Checking your GitHub sign-in…")
        self.app.github_pill.set("GitHub: checking…", "info")
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
            self.app.github_status.set("Signed in to GitHub as " + user + ".")
            self.app.github_pill.set("GitHub: " + user, "success")
            self.show_github("GitHub: " + user, "success", "Signed in to GitHub as " + user + ". Rules are submitted to "
                             + self.app.github_repository() + " for a teammate's approval.")
        elif missing:
            self.app.github_status.set("GitHub CLI isn't installed on this PC.")
            self.app.github_pill.set("GitHub CLI missing", "danger")
            self.show_github("GitHub CLI missing", "danger", "GitHub CLI isn't installed on this PC, so rules can't be "
                             "submitted yet. Ask your administrator to install it, then press F5 to check again.",
                             "Help & prerequisites", lambda: self.app.open_help())
        elif offline:
            self.app.github_status.set("Couldn't reach GitHub. Check your network connection.")
            self.app.github_pill.set("GitHub: can't reach", "warning")
            self.show_github("GitHub: can't reach", "warning", "Couldn't reach GitHub. Check your network connection, "
                             "then select Check again.", "Check again", lambda: self.check_github(force=True))
        else:
            self.app.github_status.set("Not signed in to GitHub. Select Sign in to GitHub.")
            self.app.github_pill.set("GitHub: not signed in", "warning")
            self.show_github("GitHub: not signed in", "warning", "You're not signed in to GitHub, so rules can't be "
                             "submitted yet. You can still write and check rules.", "Sign in to GitHub", self.app.github_login)

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
