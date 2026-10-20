"""Review a concrete deployment, start it explicitly, and follow the GitHub runs."""
import json
import tkinter as tk
from tkinter import ttk
import webbrowser

from . import ui_kit as kit
from .desktop_theme import COLORS, px
from .deployment_workflow import DeploymentService
from .preview_workflow import WORKFLOW


class DeploymentDialog:
    def __init__(self, parent):
        self.parent, self.app = parent, parent.app
        self.service = DeploymentService(parent.service.request)
        self.folder = self.app.rule_builder.data_dir / "deployment-requests"
        self.window = tk.Toplevel(parent.window)
        self.window.title("Review deployment"); self.window.configure(bg=COLORS["card"])
        body = ttk.Frame(self.window, style="Card.TFrame", padding=(kit.space("xl"), px(20), kit.space("xl"), px(18)))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Review deployment", style="CardTitle.TLabel").pack(anchor="w")
        kit.wrap_to_parent(ttk.Label(body, text="This creates or updates the selected rule in the listed Sentinel workspaces. "
                                                "Nothing starts until you select Start deployment.",
                                     style="CardSubtitle.TLabel", justify="left", width=1), minimum=px(200)).pack(
            fill="x", pady=(px(2), px(14)))
        # Bottom-anchored controls are packed first so they always have room; the review takes what remains.
        self.actions, buttons = kit.action_row(body, primary=("Start deployment", self.start),
                                               secondary=(("Refresh status", self.refresh), ("Open GitHub", self.open_run)),
                                               close=("Close", self.close))
        self.actions.pack(side="bottom", fill="x", pady=(px(14), 0))
        self.start_button, self.status_button, self.open_button, self.close_button = buttons
        self.buttons = tuple(buttons)
        self.start_button.configure(state="disabled"); self.status_button.configure(state="disabled")
        runs_frame, self.runs, self.runs_empty = kit.table(body, (("batch", "Batch", 60, False), ("run", "GitHub run", 120, False),
                                                                  ("state", "Last checked state", 530, True)), height=4)
        runs_frame.pack(side="bottom", fill="x")
        ttk.Label(body, text="Deployment runs", style="Section.TLabel").pack(side="bottom", anchor="w", pady=(px(10), px(6)))
        self.notice = tk.StringVar(value="Checking successful previews and the current library settings…")
        self.banner = kit.Banner(body, self.notice); self.banner.pack(side="bottom", fill="x", pady=(px(10), 0))
        review_frame, self.review = kit.text_area(body, 12, readonly=True); review_frame.pack(fill="both", expand=True)
        kit.place_dialog(self.window, parent.window, 940, 690, 800, 620)
        kit.modal(self.window, parent.window, self.close)
        self.render_runs()
        if self.record:
            self.render(); self.controls()
            self.say("Saved request for this preview. Submission and run status are shown below.")
        else:
            self.work("Checking previews and preparing the deployment…", lambda: self.service.prepare(parent.record), self.prepared)

    @property
    def record(self):
        return self.parent.deployment_record

    def say(self, text, tone="info"):
        self.notice.set(text); self.banner.tone(tone)

    def close(self):
        if self.app.busy:
            self.say("Wait for the current step to finish, then select Close.", "warning"); return
        self.window.destroy()
        if self.parent.window.winfo_exists(): self.parent.window.grab_set()

    def prepared(self, record):
        self.parent.deployment_record = record
        self.render()
        self.say("Review the exact clients and configured rule states. No deployment has been submitted.")

    def controls(self):
        self.start_button.configure(state="normal" if self.record and not self.record["attempted"] else "disabled")
        self.status_button.configure(state="normal" if self.record and any(r.get("run_id") for r in self.record["results"]) else "disabled")
        self.open_button.configure(state="normal"); self.close_button.configure(state="normal")

    def work(self, title, task, done):
        def after():
            if self.record: self.render_runs()
            self.controls()
        kit.run_in_dialog(self.app, self.window, title, task, done, controls=self.buttons, say=self.say, page="rules",
                          after=after, log_as="Rule deployment:")

    def render(self):
        plan = self.record["plan"]
        lines = ["Library: " + plan["repository"], "GitHub account: " + plan["identity"],
                 "Checked against main revision: " + plan["revision"], "Rule: " + plan["rule_path"],
                 "Mode: DEPLOY | Workspaces: " + str(len(plan["targets"])) + " | Batches: " + str(len(plan["batches"])), ""]
        for client in plan["clients"]:
            lines += [client["name"] + " | " + client["workspace"] + " | " + client["target"],
                      "  Configured rule state: " + client["rule_state"],
                      "  Subscription: " + client["subscription_id"] + " | Resource group: " + client["resource_group"]]
            if client["overrides"]: lines.append("  Assignment overrides: " + json.dumps(client["overrides"], ensure_ascii=False))
        lines += ["", "Successful preview runs: " + ", ".join(str(r["run_id"]) for r in plan["preview"]["results"]), "",
                  "GitHub runs the library's deployment workflow and enforces its configured environment protections. "
                  "If no approval is required, deployment may start immediately.",
                  "The workflow runs new preview checks before deploying. It does not assign the rule to other clients or change "
                  "its configured enabled state.",
                  "GitHub resolves main when each run starts, so it can change after this review; check each run's revision and "
                  "new preview artifacts before approving production."]
        self.review.configure(state="normal"); self.review.delete("1.0", "end")
        self.review.insert("1.0", "\n".join(lines)); self.review.configure(state="disabled")
        self.render_runs()

    def render_runs(self):
        self.runs.delete(*self.runs.get_children())
        results = self.record["results"] if self.record else []
        for i, result in enumerate(results):
            self.runs.insert("", "end", iid=str(i), values=(i + 1, result.get("run_id", "Unknown"), result["state"]))
        if results: self.runs_empty.hide()
        else: self.runs_empty.show("No deployment runs yet.")

    def start(self):
        if self.app.busy or not self.record or self.record["attempted"]: return
        self.work("Starting the deployment runs…", lambda: self.service.dispatch(self.record, self.folder),
                  lambda _: self.say("Deployment runs started. Select Refresh status, and open GitHub for the jobs and any required "
                                     "environment approvals. A started run does not mean the deployment succeeded.", "success"))

    def refresh(self):
        if self.app.busy or not self.record: return
        self.work("Reading the deployment run status from GitHub…", lambda: self.service.refresh(self.record, self.folder),
                  lambda _: self.say("Status refreshed. Open GitHub for each workspace's jobs, preview artifacts and any required "
                                     "approvals."))

    def open_run(self):
        if self.app.busy: return
        repository = self.parent.snapshot["repository"]
        url = "https://github.com/" + repository + "/actions/workflows/" + WORKFLOW
        selected = self.runs.selection()
        if selected and self.record:
            result = self.record["results"][int(selected[0])]
            if result.get("run_id"): url = "https://github.com/" + repository + "/actions/runs/" + str(result["run_id"])
        webbrowser.open(url)
