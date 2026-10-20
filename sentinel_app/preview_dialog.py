"""Choose workspaces, check a preview request, start it, and follow the GitHub runs."""
import copy
import tkinter as tk
from tkinter import ttk
import webbrowser

from . import ui_kit as kit
from .desktop_theme import COLORS, px
from .preview_workflow import PreviewService, eligible_clients, WORKFLOW
from .deployment_workflow import preview_ready
from .deployment_dialog import DeploymentDialog


class PreviewDialog:
    def __init__(self, app, snapshot, rule):
        self.app, self.snapshot, self.rule = app, copy.deepcopy(snapshot), copy.deepcopy(rule)
        self.service = PreviewService()
        self.folder = app.rule_builder.data_dir / "preview-requests"
        self.record, self.locked = None, False
        self.deployment_record = None
        self.selected, self.visible = set(), {}
        self.eligible = {c["target"] for c in eligible_clients(snapshot, rule["path"])}
        self.window = tk.Toplevel(app.root)
        self.window.title("Preview and deploy for clients"); self.window.configure(bg=COLORS["card"])
        body = ttk.Frame(self.window, style="Card.TFrame", padding=(kit.space("xl"), px(20), kit.space("xl"), px(18)))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Preview and deploy for clients", style="CardTitle.TLabel").pack(anchor="w")
        kit.wrap_to_parent(ttk.Label(body, text=rule["name"] + "  ·  " + rule["path"], style="CardSubtitle.TLabel",
                                     justify="left", width=1), minimum=px(200)).pack(fill="x", pady=(px(2), px(14)))
        self.search = tk.StringVar()
        self.search_box = ttk.Entry(body, textvariable=self.search); self.search_box.pack(fill="x")
        kit.placeholder(self.search_box, self.search, "Search clients or workspaces")
        bar = ttk.Frame(body, style="Card.TFrame"); bar.pack(fill="x", pady=px(8))
        self.all_button = ttk.Button(bar, text="Select all eligible", command=self.select_all, style="Small.Secondary.TButton")
        self.clear_button = ttk.Button(bar, text="Clear selection", command=self.clear, style="Small.Secondary.TButton")
        self.all_button.pack(side="left"); self.clear_button.pack(side="left", padx=px(8))
        self.count = tk.StringVar(); ttk.Label(bar, textvariable=self.count, style="Hint.TLabel").pack(side="left", padx=(px(8), 0))
        # Bottom-anchored controls are packed first so they always have room; the lists share what remains.
        self.actions, buttons = kit.action_row(body, primary=("Start previews", self.start),
                                               secondary=(("Check", self.prepare), ("Deploy…", self.open_deployment),
                                                          ("Refresh status", self.refresh_status), ("Open GitHub", self.open_run)),
                                               close=("Close", self.close))
        self.actions.pack(side="bottom", fill="x", pady=(px(14), 0))
        self.start_button, self.prepare_button, self.deploy_button, self.status_button, self.open_button, self.close_button = buttons
        for button in (self.start_button, self.deploy_button, self.status_button): button.configure(state="disabled")
        runs_frame, self.runs, self.runs_empty = kit.table(body, (("batch", "Batch", 60, False), ("run", "GitHub run", 120, False),
                                                                  ("state", "Last checked state", 600, True)), height=3)
        runs_frame.pack(side="bottom", fill="x")
        ttk.Label(body, text="Preview runs", style="Section.TLabel").pack(side="bottom", anchor="w", pady=(px(10), px(6)))
        self.notice = tk.StringVar(value="Choose clients, then select Check. No GitHub run starts until you select Start previews.")
        self.banner = kit.Banner(body, self.notice); self.banner.pack(side="bottom", fill="x", pady=(px(10), 0))
        frame, self.clients, self.empty = kit.table(body, (("selected", "Selected", 70, False), ("client", "Client", 160, True),
                                                           ("workspace", "Workspace", 150, True), ("target", "Target", 225, True),
                                                           ("reason", "Availability", 180, False)), height=6)
        frame.pack(fill="both", expand=True)
        self.clients.bind("<Button-1>", self.clicked)
        self.clients.bind("<space>", self.toggle_focused)
        kit.wrap_to_parent(ttk.Label(body, text="Select a row or press Space to pick it. Only enabled targets that already have "
                                                "this rule can be previewed.", style="Hint.TLabel", justify="left", width=1),
                           minimum=px(200)).pack(fill="x", pady=px(8))
        review_frame, self.review = kit.text_area(body, 6, readonly=True); review_frame.pack(fill="both", expand=True)
        self.search.trace_add("write", lambda *args: self.render_clients())
        kit.place_dialog(self.window, app.root, 1000, 760, 850, 680)
        kit.modal(self.window, app.root, self.close, primary=self.prepare_button, focus=self.search_box)
        self.render_clients(); self.render_runs()

    def say(self, text, tone="info"):
        self.notice.set(text); self.banner.tone(tone)

    def close(self):
        if self.app.busy:
            self.say("Wait for the current step to finish, then select Close.", "warning"); return
        self.window.destroy()

    def render_clients(self):
        self.clients.delete(*self.clients.get_children()); self.visible = {}
        term = self.search.get().strip().casefold()
        for i, client in enumerate(self.snapshot["clients"]):
            if term and term not in " ".join((client["name"], client["workspace"], client["target"])).casefold(): continue
            target = client["target"]; self.visible[str(i)] = target
            reason = "Ready" if target in self.eligible else "Target disabled" if not client["raw"]["enabled"] else "Rule not assigned"
            self.clients.insert("", "end", iid=str(i), values=("Yes" if target in self.selected else "", client["name"],
                                                              client["workspace"], target, reason),
                                tags=() if target in self.eligible else ("muted",))
        if self.visible: self.empty.hide()
        elif self.snapshot["clients"]: self.empty.show("No clients match your search.", ("Clear search", lambda: self.search.set("")))
        else: self.empty.show("The rule library has no client workspaces yet.")
        self.count.set(str(len(self.selected)) + " selected of " + str(len(self.eligible)) + " eligible")

    def invalidate(self):
        self.record = None; self.deployment_record = None
        self.start_button.configure(state="disabled"); self.deploy_button.configure(state="disabled")
        self.show_review(""); self.say("Selection changed — select Check again before starting previews.")
        self.render_clients(); self.render_runs()

    def toggle(self, item):
        if self.app.busy or self.locked: return
        target = self.visible.get(item)
        if target not in self.eligible: return
        if target in self.selected: self.selected.remove(target)
        else: self.selected.add(target)
        self.invalidate()
        if self.clients.exists(item): self.clients.focus(item); self.clients.selection_set(item)

    def clicked(self, event):
        item = self.clients.identify_row(event.y)
        if item: self.toggle(item); return "break"

    def toggle_focused(self, event):
        self.toggle(self.clients.focus()); return "break"

    def select_all(self):
        if not self.app.busy and not self.locked: self.selected = set(self.eligible); self.invalidate()

    def clear(self):
        if not self.app.busy and not self.locked: self.selected.clear(); self.invalidate()

    def show_review(self, text):
        self.review.configure(state="normal"); self.review.delete("1.0", "end"); self.review.insert("1.0", text)
        self.review.configure(state="disabled")

    def controls(self):
        for widget in (self.search_box, self.all_button, self.clear_button, self.prepare_button):
            widget.configure(state="disabled" if self.locked else "normal")
        self.prepare_button.configure(text="Check again" if self.record else "Check")
        self.start_button.configure(state="normal" if self.record and not self.locked else "disabled")
        self.status_button.configure(state="normal" if self.record and any(r.get("run_id") for r in self.record["results"]) else "disabled")
        self.deploy_button.configure(state="normal" if self.deployment_record or preview_ready(self.record) else "disabled")
        self.open_button.configure(state="normal"); self.close_button.configure(state="normal")

    def work(self, title, task, done):
        def after():
            if self.record:
                self.locked = self.record["attempted"]
                self.render_runs()
            self.controls()
        kit.run_in_dialog(self.app, self.window, title, task, done,
                          controls=(self.search_box, self.all_button, self.clear_button, self.prepare_button, self.start_button,
                                    self.deploy_button, self.status_button, self.open_button, self.close_button),
                          say=self.say, page="rules", after=after, log_as="Rule preview:")

    def prepare(self):
        if self.app.busy or self.locked: return
        self.record = None; self.show_review("")
        selected = sorted(self.selected)
        def done(record):
            self.record = record; plan = record["plan"]
            lines = ["Library: " + plan["repository"], "GitHub account: " + plan["identity"],
                     "Checked against main revision: " + plan["revision"], "Rule: " + plan["rule_path"],
                     "Mode: preview | Workflow runs: " + str(len(plan["batches"])), "", "Selected workspaces:"]
            lines += [c["name"] + " | " + c["workspace"] + " | " + c["target"] + " | Rule " + c["rule_state"].lower()
                      for c in plan["clients"]]
            lines += ["", "GitHub resolves main when each run starts. Select Refresh status to see the revision each run used.",
                      "This runs the library's preview workflow; it does not assign the rule to new clients."]
            self.show_review("\n".join(lines))
            self.say("Checked. Review the exact targets above, then select Start previews.", "success")
        self.work("Checking the rule library and workflow inputs…",
                  lambda: self.service.prepare(self.snapshot["repository"], self.snapshot["revision"], self.rule["path"], selected), done)

    def start(self):
        if self.app.busy or self.locked or not self.record: return
        self.work("Starting the preview runs…", lambda: self.service.dispatch(self.record, self.folder),
                  lambda value: self.say("Preview runs started. Select Refresh status for results. The request is saved on this PC in "
                                         + str(self.folder) + ".", "success"))

    def render_runs(self):
        self.runs.delete(*self.runs.get_children())
        results = self.record["results"] if self.record else []
        for i, result in enumerate(results):
            self.runs.insert("", "end", iid=str(i), values=(i + 1, result.get("run_id", "Unknown"), result["state"]))
        if results: self.runs_empty.hide()
        else: self.runs_empty.show("No preview runs yet.")

    def refresh_status(self):
        if self.app.busy or not self.record: return
        self.work("Reading the preview run status from GitHub…", lambda: self.service.refresh(self.record, self.folder),
                  lambda value: self.say("Status refreshed. Deploy becomes available once every preview succeeds at the checked "
                                         "revision. Open GitHub to inspect the preview artifacts."))

    def open_run(self):
        if self.app.busy: return
        url = "https://github.com/" + self.snapshot["repository"] + "/actions/workflows/" + WORKFLOW
        selected = self.runs.selection()
        if selected and self.record:
            result = self.record["results"][int(selected[0])]
            if result.get("run_id"): url = "https://github.com/" + self.snapshot["repository"] + "/actions/runs/" + str(result["run_id"])
        webbrowser.open(url)

    def open_deployment(self):
        if self.app.busy: return
        if not self.deployment_record and not preview_ready(self.record):
            self.say("Select Refresh status after every preview succeeds, then review the deployment.", "warning")
            return
        DeploymentDialog(self)
