"""Choose workspaces, check a preview request, start it, and follow the GitHub runs."""
import copy
import tkinter as tk
from tkinter import ttk
import webbrowser

from . import ui_kit as kit
from .assignment_dialog import ClientPicker, reveal
from .desktop_theme import COLORS, px
from .preview_workflow import PreviewService, eligible_clients, WORKFLOW
from .deployment_workflow import preview_ready
from .deployment_dialog import DeploymentDialog


class PreviewDialog(ClientPicker):
    def __init__(self, app, snapshot, rule, preselect=()):
        """``preselect`` ticks those targets to start with, where they are eligible: Drift opens the
        dialog for its own client."""
        self.app, self.snapshot, self.rule = app, copy.deepcopy(snapshot), copy.deepcopy(rule)
        self.service = PreviewService()
        self.folder = app.rule_builder.data_dir / "preview-requests"
        self.record, self.locked = None, False
        self.deployment_record = None
        self.eligible = {c["target"] for c in eligible_clients(snapshot, rule["path"])}
        self.selected, self.visible = set(preselect) & self.eligible, {}
        self.window = tk.Toplevel(app.root)
        self.window.title("Preview and deploy for clients"); self.window.configure(bg=COLORS["card"])
        body = ttk.Frame(self.window, style="Card.TFrame", padding=(kit.space("xl"), px(20), kit.space("xl"), px(18)))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Preview and deploy for clients", style="CardTitle.TLabel").pack(anchor="w")
        kit.wrap_to_parent(ttk.Label(body, text=rule["name"] + "  ·  " + rule["path"], style="CardSubtitle.TLabel",
                                     justify="left", width=1), minimum=px(200)).pack(fill="x", pady=(px(2), px(14)))
        self.search = tk.StringVar()
        self.search_box = ttk.Entry(body, textvariable=self.search); self.search_box.pack(fill="x")
        kit.placeholder(self.search_box, self.search, "Search clients or workspaces", search=True)
        bar = ttk.Frame(body, style="Card.TFrame"); bar.pack(fill="x", pady=px(8))
        self.all_button = ttk.Button(bar, text="Select all eligible", command=self.select_all, style="Small.Secondary.TButton")
        self.clear_button = ttk.Button(bar, text="Clear selection", command=self.clear, style="Small.Secondary.TButton")
        self.all_button.pack(side="left"); self.clear_button.pack(side="left", padx=px(8))
        self.count = tk.StringVar(); ttk.Label(bar, textvariable=self.count, style="Hint.TLabel").pack(side="left", padx=(px(8), 0))
        # Bottom-anchored controls are packed first so they always have room; the Check result takes what remains.
        # The steps read right to left towards Close, as in every dialog: [Deploy…] [Start previews] [Check] [Close].
        self.actions, buttons = kit.action_row(body, primary=("Deploy…", self.open_deployment),
                                               secondary=(("Start previews", self.start), ("Check", self.prepare)),
                                               close=("Close", self.close),
                                               aux=(("Refresh status", self.refresh_status), ("Open in GitHub", self.open_run)))
        self.actions.pack(side="bottom", fill="x", pady=(px(14), 0))
        self.deploy_button, self.start_button, self.prepare_button, self.close_button, self.status_button, self.open_button = buttons
        self.notice = tk.StringVar(value="Choose clients, then select Check. No GitHub run starts until you select Start previews.")
        self.banner = kit.Banner(body, self.notice); self.banner.pack(side="bottom", fill="x", pady=(px(10), 0))
        # Shown between the banner and the buttons once there are runs (render_runs).
        self.runs_frame, self.runs, _ = kit.table(body, (("batch", "Batch", 60, False), ("run", "GitHub run", 120, False),
                                                         ("state", "Last checked state", 600, True)), height=1)
        self.runs_label = ttk.Label(body, text="Preview runs", style="Section.TLabel")
        frame, self.clients, self.empty = kit.table(body, (("client", "Client", 170, True), ("workspace", "Workspace", 170, True),
                                                           ("target", "Target", 235, True), ("reason", "Availability", 180, False)),
                                                    height=max(3, min(6, len(self.snapshot["clients"]))), check=True)
        frame.pack(fill="x")
        self.pickable()
        review_frame, self.review = kit.text_area(body, 6, readonly=True,
                                                  placeholder="Select Check to see the exact workspaces and workflow runs before anything starts.")
        review_frame.pack(fill="both", expand=True, pady=(px(12), 0))
        # Packed first to keep their room at the bottom; Tab reaches them after the list, as they read.
        self.runs_label.lift(); self.runs_frame.lift(); self.banner.lift(); self.actions.lift()
        # Filter once typing pauses; Down or Enter in the search box filters now, then moves into the list.
        self.filter = kit.debounce(self.window, 150, self.render_clients)
        for key in ("<Down>", "<Return>"): self.search_box.bind(key, lambda event: self.filter.flush(), add="+")
        kit.link_search(self.search_box, self.clients)
        self.search.trace_add("write", self.filter)
        kit.place_dialog(self.window, app.root, 1000, 760, 850, 680)
        kit.modal(self.window, app.root, self.close, primary=self.prepare_button, focus=self.search_box)
        self.render_clients(); reveal(self.clients, self.visible, self.selected); self.render_runs(); self.controls()

    def say(self, text, tone="info"):
        self.notice.set(text); self.banner.tone(tone)

    def close(self):
        if self.app.busy:
            self.say("Wait for the current step to finish, then select Close.", "warning"); return
        self.window.destroy()

    def render_clients(self):
        self.filter.cancel()
        self.clients.delete(*self.clients.get_children()); self.visible = {}
        term = self.search.get().strip().casefold()
        for i, client in enumerate(self.snapshot["clients"]):
            if term and term not in " ".join((client["name"], client["workspace"], client["target"])).casefold(): continue
            target = client["target"]; self.visible[str(i)] = target
            reason = "Ready" if target in self.eligible else "Target disabled" if not client["raw"]["enabled"] else "Rule not assigned"
            self.clients.insert("", "end", iid=str(i), image=self.box(target),
                                values=(client["name"], client["workspace"], target, reason),
                                tags=() if target in self.eligible else ("muted",))
        if self.visible: self.empty.hide()
        elif self.snapshot["clients"]: self.empty.show("No clients match your search.", ("Clear search", lambda: self.search.set("")))
        else: self.empty.show("The rule library has no client workspaces yet.")
        self.paint()

    def selectable(self, target):
        return target in self.eligible

    def count_text(self):
        return str(len(self.selected)) + " selected of " + str(len(self.eligible)) + " eligible"

    def invalidate(self):
        self.record = None; self.deployment_record = None
        self.show_review(""); self.say("Selection changed — select Check again before starting previews.")
        self.render_runs(); self.controls()

    def select_all(self):
        if self.can_pick(): self.selected = set(self.eligible); self.changed(self.visible)

    def clear(self):
        if self.can_pick(): self.selected.clear(); self.changed(self.visible)

    def show_review(self, text):
        kit.set_text(self.review, text)

    def controls(self):
        for widget in (self.search_box, self.all_button, self.clear_button, self.prepare_button):
            widget.configure(state="disabled" if self.locked else "normal")
        self.prepare_button.configure(text="Check again" if self.record else "Check")
        startable, deployable = bool(self.record and not self.locked), bool(self.deployment_record or preview_ready(self.record))
        self.start_button.configure(state="normal" if startable else "disabled")
        self.status_button.configure(state="normal" if self.record and any(r.get("run_id") for r in self.record["results"]) else "disabled")
        self.deploy_button.configure(state="normal" if deployable else "disabled")
        self.open_button.configure(state="normal"); self.close_button.configure(state="normal")
        # The blue button is the next step: Check, Start previews, then Deploy… once every preview has
        # succeeded. While the runs are going, none leads: Refresh status (at the left) is next.
        lead = (self.deploy_button if deployable else self.start_button if startable else
                None if self.locked else self.prepare_button)
        kit.promote(self.window, lead, *(b for b in (self.start_button, self.prepare_button, self.deploy_button) if b is not lead))

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
        """List the preview runs above the buttons, once there are any; until then the Check result has the room."""
        self.runs.delete(*self.runs.get_children())
        results = self.record["results"] if self.record else []
        for i, result in enumerate(results):
            self.runs.insert("", "end", iid=str(i), values=(i + 1, result.get("run_id", "Unknown"), result["state"]))
        self.runs.configure(height=max(1, min(3, len(results))))
        if results and not self.runs_frame.winfo_manager():
            self.runs_frame.pack(side="bottom", fill="x", before=self.banner)
            self.runs_label.pack(side="bottom", anchor="w", pady=(px(10), px(6)), before=self.banner)
        elif not results:
            self.runs_frame.pack_forget(); self.runs_label.pack_forget()

    def refresh_status(self):
        if self.app.busy or not self.record: return
        self.work("Reading the preview run status from GitHub…", lambda: self.service.refresh(self.record, self.folder),
                  lambda value: self.say("Status refreshed. Deploy becomes available once every preview succeeds at the checked "
                                         "revision. Open in GitHub to inspect the preview artifacts."))

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
