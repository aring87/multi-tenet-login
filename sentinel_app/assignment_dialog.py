"""Choose client workspaces for a rule and submit the assignment for a teammate's approval."""
import copy
import json
import re
import tkinter as tk
from tkinter import ttk
import webbrowser

from . import ui_kit as kit
from .desktop_theme import COLORS, px
from .rule_assignments import AssignmentService, assignment_diff

PULL_URL = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+")


class AssignmentDialog:
    def __init__(self, app, snapshot=None, rule=None, record=None):
        self.app = app
        self.snapshot = copy.deepcopy(snapshot) if snapshot else {"clients": []}
        self.rule = copy.deepcopy(rule) if rule else {"name": record["plan"]["name"], "path": record["plan"]["rule_path"]}
        self.service = AssignmentService()
        self.folder = app.rule_builder.data_dir / "assignment-requests"
        self.record, self.locked = record, record is not None
        self.selected, self.visible = set(), {}
        self.window = tk.Toplevel(app.root)
        self.window.title("Assign rule to clients"); self.window.configure(bg=COLORS["card"])
        body = ttk.Frame(self.window, style="Card.TFrame", padding=(kit.space("xl"), px(20), kit.space("xl"), px(18)))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Assign rule to clients", style="CardTitle.TLabel").pack(anchor="w")
        kit.wrap_to_parent(ttk.Label(body, text=self.rule["name"] + "  ·  " + self.rule["path"], style="CardSubtitle.TLabel",
                                     justify="left", width=1), minimum=px(200)).pack(fill="x", pady=(px(2), px(14)))
        toolbar = ttk.Frame(body, style="Card.TFrame"); toolbar.pack(fill="x", pady=(0, px(10)))
        self.search = tk.StringVar(); self.search_box = ttk.Entry(toolbar, textvariable=self.search)
        self.search_box.pack(side="left", fill="x", expand=True, padx=(0, px(16)))
        kit.placeholder(self.search_box, self.search, "Search clients or workspaces")
        self.enabled = tk.StringVar(value="Disabled")
        self.state_box = ttk.Combobox(toolbar, textvariable=self.enabled, values=("Disabled", "Enabled"), state="readonly", width=10)
        self.state_box.pack(side="right"); self.state_box.bind("<MouseWheel>", lambda event: "break")
        ttk.Label(toolbar, text="Rule state for selected clients", style="Field.TLabel").pack(side="right", padx=(0, px(8)))
        # Bottom-anchored controls are packed first so they always have room; the lists share what remains.
        self.actions, buttons = kit.action_row(body, primary=("Submit for approval", self.submit),
                                               secondary=(("Check", self.prepare), ("Open in GitHub", self.open_link)),
                                               close=("Close", self.close))
        self.actions.pack(side="bottom", fill="x", pady=(px(14), 0))
        self.submit_button, self.prepare_button, self.open_button, self.close_button = buttons
        self.notice = tk.StringVar(value="Clients you don't select stay unchanged. Submitting asks a teammate to approve the "
                                         "assignment; nothing is deployed from here.")
        self.banner = kit.Banner(body, self.notice); self.banner.pack(side="bottom", fill="x", pady=(px(10), 0))
        frame, self.clients, self.empty = kit.table(body, (("pick", "Selected", 70, False), ("client", "Client", 140, True),
                                                           ("workspace", "Workspace", 150, True), ("target", "Target", 210, True),
                                                           ("assignment", "Current rule", 120, False), ("state", "Target state", 110, False)),
                                                    height=6)
        frame.pack(fill="both", expand=True)
        self.clients.bind("<Button-1>", self.clicked); self.clients.bind("<space>", self.toggle_focused)
        selection = ttk.Frame(body, style="Card.TFrame"); selection.pack(fill="x", pady=px(8))
        self.all_button = ttk.Button(selection, text="Select shown", command=self.select_shown, style="Small.Secondary.TButton")
        self.clear_button = ttk.Button(selection, text="Clear selection", command=self.clear, style="Small.Secondary.TButton")
        self.all_button.pack(side="left"); self.clear_button.pack(side="left", padx=px(8))
        self.count = tk.StringVar(); ttk.Label(selection, textvariable=self.count, style="Hint.TLabel").pack(side="left", padx=(px(8), 0))
        ttk.Label(body, text="What will change", style="Section.TLabel").pack(anchor="w", pady=(px(4), px(6)))
        review, self.text = kit.text_area(body, 10, readonly=True); review.pack(fill="both", expand=True)
        self.search.trace_add("write", lambda *args: self.render_clients())
        self.enabled.trace_add("write", lambda *args: self.invalidate())
        kit.place_dialog(self.window, app.root, 1050, 800, 900, 720)
        kit.modal(self.window, app.root, self.close, primary=self.prepare_button, focus=self.search_box)
        self.render_clients()
        if record: self.render_review()
        self.refresh_controls()

    def say(self, text, tone="info"):
        self.notice.set(text); self.banner.tone(tone)

    def close(self):
        if self.app.busy:
            self.say("Wait for the current step to finish, then select Close.", "warning"); return
        self.window.destroy()

    def render_clients(self):
        self.clients.delete(*self.clients.get_children()); self.visible = {}
        term = self.search.get().strip().casefold()
        for i, c in enumerate(sorted(self.snapshot["clients"], key=lambda c: (c["name"].casefold(), c["target"].casefold()))):
            if term and term not in " ".join((c["name"], c["workspace"], c["target"])).casefold(): continue
            self.visible[str(i)] = c["target"]
            assignment = next((a for a in c["assignments"] if a["path"] == self.rule["path"]), None)
            effective = None if assignment is None else assignment.get("overrides", {}).get("enabled", self.rule["raw"].get("enabled"))
            current = ("Unassigned" if assignment is None else "Enabled" if effective is True else
                       "Disabled" if effective is False else "Not specified")
            self.clients.insert("", "end", iid=str(i), values=("Yes" if c["target"] in self.selected else "", c["name"], c["workspace"],
                                                              c["target"], current, "Enabled" if c["raw"]["enabled"] else "Disabled"),
                                tags=() if c["raw"]["enabled"] else ("muted",))
        if self.visible: self.empty.hide()
        elif self.snapshot["clients"]: self.empty.show("No clients match your search.", ("Clear search", lambda: self.search.set("")))
        else: self.empty.show("The rule library has no client workspaces yet.")
        hidden = len(self.selected - set(self.visible.values()))
        self.count.set(str(len(self.selected)) + " selected" + (" · " + str(hidden) + " hidden by search" if hidden else ""))

    def invalidate(self):
        if self.locked or self.app.busy: return
        self.record = None; self.show(""); self.refresh_controls()
        self.say("Selection changed — select Check to see what will change. Clients you don't select stay unchanged.")
        self.render_clients()

    def toggle(self, item):
        if self.locked or self.app.busy: return
        target = self.visible.get(item)
        if target is None: return
        if target in self.selected: self.selected.remove(target)
        else: self.selected.add(target)
        self.invalidate()
        if self.clients.exists(item): self.clients.focus(item); self.clients.selection_set(item)

    def clicked(self, event):
        item = self.clients.identify_row(event.y)
        if item: self.toggle(item); return "break"

    def toggle_focused(self, event):
        self.toggle(self.clients.focus()); return "break"

    def select_shown(self):
        if not self.locked and not self.app.busy: self.selected.update(self.visible.values()); self.invalidate()

    def clear(self):
        if not self.locked and not self.app.busy: self.selected.clear(); self.invalidate()

    def show(self, value):
        self.text.configure(state="normal"); self.text.delete("1.0", "end"); self.text.insert("1.0", value)
        self.text.configure(state="disabled")

    def refresh_controls(self):
        for widget in (self.search_box, self.all_button, self.clear_button, self.prepare_button):
            widget.configure(state="disabled" if self.locked else "normal")
        self.state_box.configure(state="disabled" if self.locked else "readonly")
        self.prepare_button.configure(text="Check again" if self.record else "Check")
        self.submit_button.configure(state="normal" if self.record and not self.record.get("pull_request") else "disabled")
        self.open_button.configure(state="normal" if self.record and self.record.get("pull_request") else "disabled")
        self.close_button.configure(state="normal")

    def work(self, title, task, done):
        def on_error(error):
            self.say(error + (" Your request is saved; select Submit for approval again to finish it. Recovery files are in "
                              + str(self.folder) + "." if self.locked else ""), "danger")
        kit.run_in_dialog(self.app, self.window, title, task, done,
                          controls=(self.search_box, self.state_box, self.all_button, self.clear_button, self.prepare_button,
                                    self.submit_button, self.open_button, self.close_button),
                          say=self.say, page="rules", on_error=on_error, after=self.refresh_controls, log_as="Rule assignment:")

    def prepare(self):
        if self.app.busy or self.locked: return
        self.record = None; self.show("")
        targets = sorted(self.selected); enabled = self.enabled.get() == "Enabled"
        def done(record): self.record = record; self.render_review()
        self.work("Comparing client files with the rule library. Nothing is sent to GitHub yet…",
                  lambda: self.service.prepare(self.snapshot["repository"], self.snapshot["revision"], self.rule["path"], targets, enabled),
                  done)

    def render_review(self):
        p = self.record["plan"]
        lines = ["Library: " + p["repository"], "GitHub account: " + p["identity"], "Checked against main: " + p["base_sha"],
                 "Rule: " + p["rule_path"], "Requested rule state: " + ("Enabled" if p["enabled"] else "Disabled"),
                 "Changed client files: " + str(len(p["files"])), ""]
        for c in p["impact"]:
            lines.append(c["target"] + " | " + c["workspace"] + " | " + c["action"] + " | target "
                         + ("enabled" if c["target_enabled"] else "disabled") + " | overrides: " + json.dumps(c["overrides"]))
        lines += ["", *p["warnings"], "", assignment_diff(p)]
        self.show("\n".join(lines))
        if self.record.get("pull_request"):
            self.say("Submitted for approval: " + self.record["pull_request"], "success")
        else:
            self.say("Check the file changes below, then select Submit for approval. Your team's GitHub checks may run; "
                     "nothing is deployed.")

    def submit(self):
        if self.app.busy or not self.record or self.record.get("pull_request"): return
        self.locked = True
        def done(url):
            self.say("Submitted for approval: " + url, "success")
            self.app.catalog_page.note.set("Assignment submitted: " + url + ". After it is approved and merged, select Refresh "
                                           "library before previewing.")
        self.work("Submitting the assignment for approval…", lambda: self.service.submit(self.record, self.folder), done)

    def open_link(self):
        if self.record and self.record.get("pull_request"):
            url = self.record["pull_request"]
            if PULL_URL.fullmatch(url): webbrowser.open(url)
